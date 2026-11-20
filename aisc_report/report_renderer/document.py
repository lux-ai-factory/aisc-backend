"""The report document: the pilot's Jinja template and CSS, WeasyPrint and the plugin ABI.

`build_sections` turns the frozen snapshot into the pilot's section list (cover, chapters,
appendix, the block fragments), `render_html` fills `templates/document.html.j2` and
`render_pdf`/`render_docx` draw it with WeasyPrint / python-docx. Nothing is read from the
project here: every value comes from the snapshot, so a published report does not change.
"""
from __future__ import annotations

import base64
import html as html_module
import math
import re
from pathlib import Path
from types import SimpleNamespace

from aisc_plugin_interface.report.charts import svg as chart_svg
from aisc_plugin_interface.report.i18n import fill
from aisc_plugin_interface.report.templating import render_package_template
from asgiref.sync import sync_to_async
from markupsafe import Markup

from aisc_evaluation.models import Evaluation

from . import docx as docx_module
from . import errors
from . import pdf as pdf_module
from . import style as style_module
from . import structure
from . import svg_print
from . import tools_abi

PACKAGE = "aisc_report.report_renderer"

CSS_PATH = Path(__file__).resolve().parent / "templates" / "report.css"
FEATURE_CSS_DIR = Path(__file__).resolve().parent / "templates" / "css"

#: the charts' colours (the pilot's theme/brand.json primary and accent)
CHART_COLOURS = {"primary": "#000FDF", "text": "#000000", "grid": "#CCCCCC"}
DEFAULT_ACCENT = "#FF007E"

#: the page margins' texts (the pilot's margin_texts)
_PLACEHOLDER = re.compile(r"\{(project|version|release|layout|date)\}")

_TITLE_DEFAULTS = {
    "cover": "",
    "chapter": "Chapter",
    "appendix": "Appendix",
    "ai_card": "AI card",
    "control_objectives": "Control objectives",
    "control_answers": "Checklists",
    "test_results": "Test results",
    "test_runs": "Test runs",
    "summary_coverage": "Evidence coverage",
    "key_figures": "Key figures",
    "changes_since": "Changes since",
    "risk_classification": "Risk classification",
    "chart": "Chart",
    "dashboard_chart": "Results by evaluation",
    "free_text": "",
    "amendments": "Amendments",
}


def style_of(snapshot: dict) -> dict | None:
    """The document style: the first block option carrying one (the cover's, normally)."""
    for block in snapshot.get("blocks") or []:
        options = block.get("options") or {}
        if isinstance(options.get("style"), dict):
            return options["style"]
    return None


def _t(language: str = "en"):
    return lambda message, **params: fill(message, params)


def _render(template: str, **context) -> str:
    return render_package_template(PACKAGE, f"templates/{template}", **context)


def _block_render(template: str, **context) -> str:
    return render_package_template(PACKAGE, f"blocks/templates/{template}", **context)


def _escape(value) -> str:
    return html_module.escape("" if value is None else str(value))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_escape(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# --- block fragments ------------------------------------------------------------


def _section(block: dict, snapshot: dict, language: str) -> dict:
    """One layout block as a pilot section. `html` is filled in later for the async ones."""
    options = block.get("options") or {}
    type_id = block.get("block_type") or ""
    title = options.get("title") or _TITLE_DEFAULTS.get(type_id, type_id.replace("_", " ").title())
    kind = None
    if type_id == "cover":
        kind = "cover"
    elif type_id == "chapter":
        kind = "chapter"
    elif type_id == "appendix":
        kind = "appendix"
    return {
        "instance_id": block.get("instance_id") or type_id,
        "type_id": type_id,
        "kind": kind,
        "title": title,
        "html": "",
        "status": "ok",
        "page_break": bool(options.get("page_break_before")),
        "notices": [],
        "commentary": options.get("commentary") or "",
        "commentary_position": options.get("commentary_position") or "after",
        "lead": "",
        "options": options,
    }


async def _fragment(section: dict, snapshot: dict, language: str, t) -> str:
    options = section["options"]
    type_id = section["type_id"]
    if type_id == "cover":
        return _cover(options, snapshot, t)
    if type_id in ("chapter", "appendix"):
        return ""
    if type_id == "free_text":
        return _free_text(options, t)
    if type_id == "amendments":
        return _free_text({"text": options.get("text") or options.get("amendments")}, t)
    if type_id == "key_figures":
        return _block_render("key_figures.html.j2", tiles=_tiles(options, snapshot), t=t)
    if type_id == "summary_coverage":
        return _summary_coverage(options, snapshot, t)
    if type_id == "test_results":
        return await _test_results(snapshot, language, t)
    if type_id == "test_runs":
        return _test_runs(options, snapshot, t)
    if type_id in ("chart", "dashboard_chart"):
        return _chart(options, snapshot, type_id, t)
    if type_id == "ai_card":
        return _ai_card(snapshot, t)
    if type_id == "control_objectives":
        return _control_objectives(snapshot, t)
    if type_id == "control_answers":
        return _control_answers(snapshot, t)
    if type_id == "changes_since":
        return _changes_since(snapshot, t)
    if type_id == "risk_classification":
        return _risk_classification(snapshot, t)
    return _block_render("free_text.html.j2", paragraphs=[t("This block has no renderer.")], t=t)


def _logo(snapshot: dict, options: dict):
    """(data URI, mime, bytes) for the logo, or None. The layout's own logo wins over the style's."""
    style = options.get("style") if isinstance(options.get("style"), dict) else {}
    logo = options.get("logo") or style.get("logo")
    if not isinstance(logo, dict):
        return None
    problem = style_module.logo_problem({"logo": logo})
    if problem is not None:
        return None
    mime = logo.get("mime")
    try:
        data = base64.b64decode(logo.get("data_base64") or "", validate=True)
    except Exception:                                                # noqa: BLE001 (validated above)
        return None
    if mime == "image/svg+xml":
        data = svg_print.for_print(data.decode("utf-8", "replace")).encode("utf-8")
        mime = "image/svg+xml"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", mime, data


def _cover(options: dict, snapshot: dict, t) -> str:
    style = options.get("style") if isinstance(options.get("style"), dict) else {}
    marking_key = style.get("marking") or options.get("marking")
    marking = style_module.MARKING_LABELS.get(marking_key) if marking_key else None
    logo = _logo(snapshot, options)
    system = snapshot.get("system") or {}
    version = system.get("version") or {}
    project = snapshot.get("project") or {}
    layout = snapshot.get("layout") or {}
    return _block_render(
        "cover.html.j2",
        options={
            "report_title": options.get("report_title") or project.get("name") or t("Report"),
            "subtitle": options.get("subtitle") or "",
        },
        logo=logo[0] if logo else None,
        marking=marking,
        project={"name": project.get("name") or ""},
        system={
            "name": system.get("name") or "",
            "number": version.get("number"),
            "version": version.get("name"),
            "provider": version.get("provider"),
        },
        card_revision=version.get("number") or 1,
        generated=(snapshot.get("generated_at") or "")[:10],
        generated_by=snapshot.get("generated_by") or "",
        report={"layout_name": layout.get("name") or "", "revision": layout.get("revision") or 0},
        banner=options.get("banner") or "",
        t=t,
    )


def _free_text(options: dict, t) -> str:
    text = options.get("text") or options.get("body") or ""
    paragraphs = [p.strip() for p in str(text).split("\n\n") if p.strip()]
    return _block_render("free_text.html.j2", paragraphs=paragraphs, t=t)


def _tiles(options: dict, snapshot: dict) -> list[dict]:
    if isinstance(options.get("tiles"), list) and options["tiles"]:
        return [
            {"id": str(tile.get("id") or i), "label": tile.get("label") or "",
             "value": tile.get("value"), "note": tile.get("note") or ""}
            for i, tile in enumerate(options["tiles"])
        ]
    coverage = snapshot.get("coverage") or []
    linked = sum(1 for row in coverage if row.get("linked"))
    evaluations = snapshot.get("evaluations") or []
    observations = sum((e.get("key_results") or {}).get("observations", 0) for e in evaluations)
    return [
        {"id": "objectives", "label": "Objectives covered", "value": f"{linked}/{len(coverage)}",
         "note": ""},
        {"id": "controls", "label": "Checklists installed", "value": len(snapshot.get("controls") or []),
         "note": ""},
        {"id": "evaluations", "label": "Evaluations", "value": len(evaluations), "note": ""},
        {"id": "measurements", "label": "Measurements", "value": observations, "note": ""},
    ]


def _summary_coverage(options: dict, snapshot: dict, t) -> str:
    rows = [
        {
            "id": row.get("objective_id"),
            "label": row.get("text") or "",
            "description": row.get("text") or "",
            "tests": row.get("tests") or [],
            "checklists": row.get("controls") or [],
            "status": "covered" if row.get("linked") else "gap",
        }
        for row in snapshot.get("coverage") or []
    ]
    review = None
    reviewed_by = next((r.get("reviewed_by") for r in (snapshot.get("coverage") or []) if r.get("reviewed_by")), None)
    if reviewed_by:
        review = ["reviewed", t("Reviewed by {name}", name=reviewed_by)]
    return _block_render("summary_coverage.html.j2", rows=rows, review=review, merge_label=False, t=t)


async def _test_results(snapshot: dict, language: str, t) -> str:
    evaluations = []
    for entry in snapshot.get("evaluations") or []:
        evaluation = await Evaluation.objects.filter(pid=entry.get("pid")).afirst()
        runs = []
        if evaluation is not None:
            for run in await tools_abi.tool_runs(evaluation):
                # the plugin renderer may load plugin code (a blocking import, like
                # _declared_charts): keep it off the event loop
                section, _name = await sync_to_async(tools_abi.render_run)(
                    run, tools_abi.make_ctx(language)
                )
                # the renderer returns HTML: print it, do not escape it again
                section = Markup(section)
                components = ", ".join(c["name"] for c in run.components)
                runs.append(
                    {
                        "tool": run.tool.get("display_name") or run.tool.get("name") or "",
                        "target": "",
                        "components": components,
                        "status": run.evaluation_plugin.get("status") or "",
                        "no_metrics": not run.measurements,
                        "section": section,
                        "artifacts": [
                            {"name": a["name"], "file_size": a.get("file_size")} for a in run.artifacts
                        ],
                    }
                )
        key = entry.get("key_results") or {}
        evaluations.append(
            {
                "name": entry.get("pid"),
                "status": entry.get("status") or "",
                "date": (entry.get("created_at") or "")[:19].replace("T", " "),
                "short": str(entry.get("pid"))[:8],
                "runs": runs,
                # the pilot prints observations not tied to any tool here; the unified app
                # cannot tell an untagged observation from a run's, so it prints none.
                "unmatched": "",
            }
        )
    return _block_render("test_results.html.j2", evaluations=evaluations, t=t)


def _test_runs(options: dict, snapshot: dict, t) -> str:
    runs = [
        {
            "date": (e.get("created_at") or "")[:19].replace("T", " "),
            "version": (snapshot.get("system", {}).get("version") or {}).get("number") or "",
            "status": e.get("status") or "",
            "tools": [{"name": t("run")}],
            "short": str(e.get("pid"))[:8],
        }
        for e in snapshot.get("evaluations") or []
    ]
    detail = "summary" if options.get("detail") == "summary" else "full"
    return _block_render("test_runs.html.j2", runs=runs, options={"detail": detail}, t=t)


def _chart(options: dict, snapshot: dict, type_id: str, t) -> str:
    series = []
    for tile in _tiles(options, snapshot):
        value = tile.get("value")
        number = float(value) if isinstance(value, (int, float)) else None
        series.append({"label": str(tile.get("label")), "value": number})
    spec = {"kind": "hbar", "value_format": "number", "series": series, "title": options.get("title") or ""}
    try:
        svg = chart_svg(spec, CHART_COLOURS)
    except ValueError:
        svg = None
    rows = [[item["label"], "" if item["value"] is None else item["value"]] for item in series]
    return _block_render(
        "chart.html.j2",
        svg=svg or "",
        kind="hbar",
        title=options.get("title") or t(_TITLE_DEFAULTS.get(type_id, "Chart")),
        rows=rows,
        show_table=bool(options.get("show_table")),
        t=t,
    )


def _ai_card(snapshot: dict, t) -> str:
    system = snapshot.get("system") or {}
    version = system.get("version") or {}
    rows = [
        [t("Name"), system.get("name") or ""],
        [t("Version"), version.get("number")],
        [t("Provider"), version.get("provider")],
    ]
    return _table([t("Field"), t("Value")], rows)


def _control_objectives(snapshot: dict, t) -> str:
    objectives = snapshot.get("objectives") or {}
    rows = [
        [p.get("objective_id"), p.get("text"), t("yes") if p.get("key") else t("no"), p.get("score")]
        for p in objectives.get("priorities") or []
    ]
    return _table([t("Objective"), t("Description"), t("Key"), t("Priority")], rows)


def _control_answers(snapshot: dict, t) -> str:
    rows = [
        [c.get("name"), c.get("pid"), c.get("readiness"), c.get("covered")]
        for c in snapshot.get("controls") or []
    ]
    return _table([t("Checklist"), t("Reference"), t("Readiness"), t("Covered")], rows)


def _changes_since(snapshot: dict, t) -> str:
    comparison = snapshot.get("comparison")
    if not comparison:
        return _block_render("free_text.html.j2", paragraphs=[t("Nothing to compare with.")], t=t)
    rows = [[g.get("objective_id"), g.get("text"), t("gained")] for g in comparison.get("gained") or []]
    rows += [[l.get("objective_id"), l.get("text"), t("lost")] for l in comparison.get("lost") or []]
    for c in comparison.get("changed") or []:
        rows.append([c.get("objective_id"), ", ".join(c.get("added") or []), ", ".join(c.get("removed") or [])])
    version = comparison.get("version") or {}
    body = _table([t("Objective"), t("Detail"), t("Change")], rows)
    return f"<p class='meta'>{t('Compared with version {number}', number=version.get('number'))}</p>{body}"


def _risk_classification(snapshot: dict, t) -> str:
    risks = snapshot.get("risks") or []
    rows = [[r.get("name") or r.get("risk"), r.get("classification") or r.get("level")] for r in risks]
    if not rows:
        return _block_render("free_text.html.j2", paragraphs=[t("No risks are recorded.")], t=t)
    return _table([t("Risk"), t("Classification")], rows)


# --- the document ---------------------------------------------------------------


async def build_sections(snapshot: dict, *, language: str = "en") -> list[dict]:
    t = _t(language)
    blocks = snapshot.get("blocks") or []
    if not blocks:
        blocks = [
            {"instance_id": "cover", "block_type": "cover", "options": {}},
            {"instance_id": "coverage", "block_type": "summary_coverage", "options": {}},
            {"instance_id": "results", "block_type": "test_results", "options": {}},
        ]
    sections = [_section(block, snapshot, language) for block in blocks]
    for section in sections:
        # Markup so the document template prints the fragment, not its escaped text
        section["html"] = Markup(await _fragment(section, snapshot, language, t))
    return sections


def _css_string(value: str) -> str:
    """A CSS double-quoted string that shows `value` and can never be left early."""
    text = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "<" or not ch.isprintable():
            out.append(f"\\{ord(ch):x} ")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _fill_placeholders(text: str, values: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1)) or ""), text)


def _page_label(template: str) -> str:
    parts = []
    for piece in re.split(r"(\{page\}|\{pages\})", template):
        if piece == "{page}":
            parts.append("counter(page)")
        elif piece == "{pages}":
            parts.append("counter(pages)")
        elif piece:
            parts.append(_css_string(piece))
    return " ".join(parts)


def _page_css(project_name: str, number, t, *, style: dict | None = None) -> str:
    """The @page rule: A4, the header and footer, the marking and the page label."""
    style = style or {}
    version = "" if number is None else number
    header = style.get("header_text")
    footer = style.get("footer_text")
    marking = style.get("marking") or "none"
    top_left = (
        _fill_placeholders(header, {"project": project_name, "version": version})
        if header is not None
        else t("{project}, Version {version}", project=project_name, version=version)
    )
    boxes = [f" @top-left {{ content: {_css_string(top_left)}; font-size: 9pt }}"]
    label = style_module.MARKING_LABELS.get(marking)
    if label:
        accent = style.get("accent_color")
        colour = f"; color: {accent}" if accent and re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else ""
        boxes.append(f" @top-right {{ content: {_css_string(t(label).upper())}{colour}; font-size: 9pt }}")
    if footer:
        boxes.append(f" @bottom-left {{ content: {_css_string(footer)}; font-size: 9pt }}")
    boxes.append(f" @bottom-right {{ content: {_page_label(t('Page {page} of {pages}'))}; font-size: 9pt }} }}")
    return "@page { size: A4; margin: 22mm 16mm 20mm 16mm;" + "".join(boxes)


def _feature_css(names: set[str]) -> str:
    """The CSS of the features a document uses, always in the same order."""
    out = ""
    for name in ("commentary", "chapters", "marking", "key_figures", "chart", "toc_pages", "numbering"):
        if name in names:
            out += (FEATURE_CSS_DIR / f"{name}.css").read_text(encoding="utf-8")
    return out


def _features(sections: list[dict], plan, style: dict | None) -> set[str]:
    features: set[str] = set()
    if any(s["commentary"] or s.get("lead") for s in sections):
        features.add("commentary")
    if any(s["kind"] for s in sections):
        features.add("chapters")
    if (style or {}).get("marking") in style_module.MARKING_LABELS:
        features.add("marking")
    for type_id in ("key_figures", "chart"):
        if any(s["type_id"] == type_id for s in sections):
            features.add(type_id)
    if plan.toc_entries is not None:
        features.add("toc_pages")
        features.add("numbering")
    return features


async def render_html(snapshot: dict, *, style: dict | None = None, language: str = "en") -> str:
    t = _t(language)
    style = style if style is not None else style_of(snapshot)
    sections = await build_sections(snapshot, language=language)
    layout = snapshot.get("layout") or {}
    plan = structure.plan(
        sections,
        toc="off" if layout.get("show_index") is False else "on",
        numbering=bool(layout.get("numbering", True)),
    )
    style = style or {}
    project = snapshot.get("project") or {}
    version = (snapshot.get("system") or {}).get("version") or {}
    css = (
        CSS_PATH.read_text(encoding="utf-8")
        + "\n"
        + _page_css(project.get("name") or "", version.get("number"), t, style=style)
        + "\n"
        + (style_module.css_overrides(style) if style else "")
        + _feature_css(_features(sections, plan, style))
    )
    title = f"{project.get('name') or ''} — {t('Report')}".strip(" —")
    return _render(
        "document.html.j2",
        language=language,
        title=title,
        css=Markup(css),
        banner="",
        toc=plan.toc_entries,
        toc_after=plan.toc_after,
        sections=sections,
        t=t,
    )


async def render_pdf(snapshot: dict, *, style: dict | None = None, language: str = "en") -> bytes:
    html = await render_html(snapshot, style=style, language=language)
    return pdf_module.html_to_pdf(html)


async def render_docx(snapshot: dict, *, style: dict | None = None, language: str = "en") -> bytes:
    sections = await build_sections(snapshot, language=language)
    return docx_module.sections_to_docx(snapshot, sections)
