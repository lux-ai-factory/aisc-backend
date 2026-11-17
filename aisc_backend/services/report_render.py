"""
Render a frozen report snapshot into a real document.

One place turns the snapshot (project + AI-card version + controls + objectives +
evaluations + layout blocks) into a neutral **document model** — a flat list of
elements — and then draws it three ways: HTML (the composer's preview), PDF
(`reportlab`) and DOCX (`python-docx`). Because every format is drawn from the
same model, the preview and the generated documents agree, and the block order
the layout chose is the order printed.

The renderer never reads the database: it is handed a prepared snapshot, so a
report drawn from a stored snapshot is byte-stable (nothing it reads can change).
"""
from __future__ import annotations

import html
import io

from aisc_backend.services import report_blocks

#: document-model element kinds
#: ("title", text) ("heading", level, text) ("paragraph", text)
#: ("bullets", [str]) ("kv", [(k, v)]) ("table", [header], [[cell]])
#: ("pagebreak",)

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html; charset=utf-8",
}
FORMATS = ("pdf", "docx", "html")


def _s(value) -> str:
    """A cell as a printable string (None -> '')."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


# --- the document model ---------------------------------------------------------


def build_document(snapshot: dict) -> list[tuple]:
    """The neutral document model for a snapshot, driven by its layout blocks."""
    blocks = snapshot.get("blocks")
    if not blocks:
        blocks = [{"block_type": "cover", "options": {}}]
    doc: list[tuple] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        handler = _BLOCK_HANDLERS.get(b.get("block_type"))
        if handler is None:
            continue
        doc.extend(handler(b.get("options") or {}, snapshot))
    return doc


def _project_name(snapshot: dict) -> str:
    system = snapshot.get("system") or {}
    project = snapshot.get("project") or {}
    return system.get("name") or project.get("name") or "AI system"


def _cover(options: dict, snapshot: dict) -> list[tuple]:
    project = snapshot.get("project") or {}
    system = snapshot.get("system") or {}
    version = (system.get("version") or {})
    selection = snapshot.get("selection") or {}
    rows = [
        ("Project", project.get("name")),
        ("AI card version", version.get("number")),
        ("Provider", version.get("provider")),
        ("Period", selection.get("period")),
        ("Generated", snapshot.get("generated_at")),
    ]
    return [
        ("title", _project_name(snapshot)),
        ("paragraph", project.get("description") or ""),
        ("kv", [(k, v) for k, v in rows if v not in (None, "")]),
        ("pagebreak",),
    ]


def _chapter(options: dict, snapshot: dict) -> list[tuple]:
    return [("heading", 1, _s(options.get("title")) or "Chapter")]


def _appendix(options: dict, snapshot: dict) -> list[tuple]:
    return [("pagebreak",), ("heading", 1, _s(options.get("title")) or "Appendix")]


def _free_text(options: dict, snapshot: dict) -> list[tuple]:
    out: list[tuple] = []
    title = _s(options.get("title"))
    if title:
        out.append(("heading", 2, title))
    text = _s(options.get("text"))
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if options.get("format") == "markdown":
            para = para.lstrip("# ").strip()
        out.append(("paragraph", para))
    return out


def _key_figures(options: dict, snapshot: dict) -> list[tuple]:
    objectives = snapshot.get("objectives") or {}
    evaluations = snapshot.get("evaluations") or []
    coverage = snapshot.get("coverage") or []
    system = snapshot.get("system") or {}
    version = (system.get("version") or {})
    linked = sum(1 for c in coverage if c.get("linked"))
    rows = [
        ("AI card version", version.get("number")),
        ("Control objectives", len(objectives.get("priorities") or [])),
        ("Key objectives", len(objectives.get("key_objectives") or [])),
        ("Risks", len(objectives.get("risks") or [])),
        ("Objectives with evidence", f"{linked} of {len(coverage)}"),
        ("Tests run", len(evaluations)),
    ]
    return [("heading", 2, "Key figures"), ("kv", [(k, v) for k, v in rows if v not in (None, "")])]


def _summary_coverage(options: dict, snapshot: dict) -> list[tuple]:
    coverage = snapshot.get("coverage") or []
    uncovered_only = bool(options.get("show_uncovered_only"))
    rows = []
    for c in coverage:
        if uncovered_only and c.get("linked"):
            continue
        rows.append([
            c.get("objective_id"),
            "Yes" if c.get("key") else "",
            "Yes" if c.get("linked") else "No",
            ", ".join(c.get("tests") or []) or "—",
            ", ".join(c.get("controls") or []) or "—",
        ])
    header = ["Objective", "Key", "Has evidence", "Linked tests", "Linked checklists"]
    title = "Uncovered objectives" if uncovered_only else "Objective coverage"
    out: list[tuple] = [("heading", 2, title)]
    if rows:
        out.append(("table", header, rows))
    else:
        out.append(("paragraph", "Every objective has evidence." if uncovered_only else "No objectives selected."))
    reviews = [c.get("reviewed_by") for c in coverage if c.get("reviewed_by")]
    if reviews:
        out.append(("paragraph", f"Evidence map reviewed by {reviews[0]}."))
    return out


def _control_objectives(options: dict, snapshot: dict) -> list[tuple]:
    objectives = snapshot.get("objectives") or {}
    priorities = objectives.get("priorities") or []
    header = ["Rank", "Objective", "Key", "Severity", "Risks", "Objective"]
    rows = []
    for p in priorities:
        rows.append([
            p.get("rank"),
            p.get("objective_id"),
            "Yes" if p.get("key") else "",
            p.get("top_rating"),
            len(p.get("risk_ids") or []),
            p.get("text"),
        ])
    out: list[tuple] = [("heading", 2, "Control objectives")]
    if rows:
        out.append(("table", header, rows))
    else:
        out.append(("paragraph", "No control objectives are selected for this project."))
    if options.get("show_rationale"):
        for p in priorities:
            reasons = p.get("reasons") or []
            if reasons:
                out.append(("paragraph", f"{p.get('objective_id')}: " + "; ".join(map(str, reasons))))
    return out


def _control_answers(options: dict, snapshot: dict) -> list[tuple]:
    controls = snapshot.get("controls") or []
    show_scores = bool(options.get("show_scores"))
    show_unanswered = bool(options.get("show_unanswered"))
    out: list[tuple] = [("heading", 2, "Checklist answers")]
    if not controls:
        out.append(("paragraph", "No checklist is installed."))
        return out
    for cl in controls:
        out.append(("heading", 3, _s(cl.get("name"))))
        facts = [
            ("Status", cl.get("status")),
            ("Version", cl.get("version")),
            ("Coverage", cl.get("coverage")),
            ("Readiness", cl.get("readiness")),
            ("Mean score", cl.get("mean_score")),
        ]
        out.append(("kv", [(k, v) for k, v in facts if v not in (None, "")]))
        rows = []
        for a in cl.get("answers") or []:
            if not show_unanswered and a.get("score") is None and not a.get("answer"):
                continue
            rows.append([
                a.get("order"),
                a.get("text"),
                a.get("answer"),
                a.get("score") if show_scores else "",
            ])
        if rows:
            header = ["#", "Question", "Answer", "Score" if show_scores else ""]
            out.append(("table", header, rows))
    return out


def _test_runs(options: dict, snapshot: dict) -> list[tuple]:
    evaluations = snapshot.get("evaluations") or []
    out: list[tuple] = [("heading", 2, "Test runs")]
    if not evaluations:
        out.append(("paragraph", "No evaluation was run for this version."))
        return out
    rows = [[e.get("pid", "")[:8], e.get("status"), e.get("created_at")] for e in evaluations]
    out.append(("table", ["Evaluation", "Status", "Run at"], rows))
    return out


def _test_results(options: dict, snapshot: dict) -> list[tuple]:
    evaluations = snapshot.get("evaluations") or []
    detail = options.get("detail", "summary")
    out: list[tuple] = [("heading", 2, "Test results")]
    if not evaluations:
        out.append(("paragraph", "No results to show."))
        return out
    for e in evaluations:
        kr = e.get("key_results")
        out.append(("heading", 3, f"Evaluation {e.get('pid', '')[:8]}"))
        if not kr:
            out.append(("paragraph", "This evaluation has no measurements."))
            continue
        out.append(("kv", [
            ("Observations", kr.get("observations")),
            ("Mean score", kr.get("mean_score")),
            ("Status", e.get("status")),
        ]))
        metrics = kr.get("metrics") or []
        if metrics:
            out.append(("table", ["Metric", "Mean", "Min", "Max", "N"],
                        [[m.get("name"), m.get("mean"), m.get("min"), m.get("max"), m.get("n")] for m in metrics]))
        if detail == "full":
            for dim in kr.get("detailed_measurements") or []:
                out.append(("heading", 3, f"By {dim.get('key')}"))
                out.append(("table", ["Group", "Mean", "N"],
                            [[g.get("label"), g.get("mean"), g.get("n")] for g in dim.get("groups") or []]))
    return out


def _ai_card(options: dict, snapshot: dict) -> list[tuple]:
    system = snapshot.get("system") or {}
    card = system.get("card")
    out: list[tuple] = [("heading", 2, "AI card")]
    if not card:
        out.append(("paragraph", "No AI card has been saved for this version."))
        return out
    rows = []
    for key in ("name", "description", "intended_purpose", "provider", "version"):
        if card.get(key):
            rows.append((key.replace("_", " ").title(), card.get(key)))
    if options.get("show_tags") and card.get("tags"):
        rows.append(("Tags", ", ".join(map(str, card.get("tags") or []))))
    if options.get("show_components") and card.get("components"):
        rows.append(("Components", len(card.get("components") or [])))
    if rows:
        out.append(("kv", rows))
    else:
        out.append(("paragraph", "The AI card carries no summary fields."))
    return out


def _risk_classification(options: dict, snapshot: dict) -> list[tuple]:
    objectives = snapshot.get("objectives") or {}
    risks = objectives.get("risks") or []
    out: list[tuple] = [("heading", 2, "Risk classification")]
    if not risks:
        out.append(("paragraph", "No risk has been recorded."))
        return out
    rows = [[r.get("position"), r.get("name"), r.get("rating"), r.get("comment")] for r in risks]
    out.append(("table", ["#", "Risk", "Rating", "Comment"], rows))
    return out


def _chart(options: dict, snapshot: dict) -> list[tuple]:
    dataset = options.get("dataset", "coverage_status")
    out: list[tuple] = [("heading", 2, "Chart")]
    if dataset == "checklist_scores":
        rows = [[c.get("name"), c.get("coverage"), c.get("readiness"), c.get("mean_score")]
                for c in snapshot.get("controls") or []]
        if rows:
            out.append(("table", ["Checklist", "Coverage", "Readiness", "Mean"], rows))
        else:
            out.append(("paragraph", "No checklist is installed."))
        return out
    coverage = snapshot.get("coverage") or []
    rows = [[c.get("objective_id"), "covered" if c.get("linked") else "gap"] for c in coverage]
    if rows:
        out.append(("table", ["Objective", "Status"], rows))
    else:
        out.append(("paragraph", "No objectives selected."))
    return out


def _placeholder(title: str, note: str):
    def handler(options: dict, snapshot: dict) -> list[tuple]:
        return [("heading", 2, title), ("paragraph", note)]
    return handler


_BLOCK_HANDLERS = {
    "cover": _cover,
    "chapter": _chapter,
    "appendix": _appendix,
    "free_text": _free_text,
    "key_figures": _key_figures,
    "summary_coverage": _summary_coverage,
    "control_objectives": _control_objectives,
    "control_answers": _control_answers,
    "test_runs": _test_runs,
    "test_results": _test_results,
    "ai_card": _ai_card,
    "risk_classification": _risk_classification,
    "chart": _chart,
    "changes_since": _placeholder(
        "Changes since", "Version comparison is not computed in this build; see the two versions' reports."
    ),
    "amendments": _placeholder("Amendments", "No amendments are recorded for this report."),
    "dashboard_chart": _placeholder(
        "Dashboard chart", "This chart is drawn by a results dashboard, not the report renderer."
    ),
}


# --- HTML (the composer's preview) ---------------------------------------------


def _html_escape(value) -> str:
    return html.escape(_s(value))


def render_html(model: list[tuple], title: str) -> bytes:
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        f"<title>{_html_escape(title)}</title>",
        "<style>body{font-family:'Raleway',system-ui,sans-serif;color:#0b0b2e;max-width:820px;margin:0 auto;padding:32px}"
        "h1,h2,h3{font-family:'MuseoModerno',sans-serif;color:#000079}"
        "table{border-collapse:collapse;width:100%;margin:8px 0 18px}"
        "th,td{border:1px solid #cccff9;padding:6px 8px;text-align:left;font-size:13px}"
        "th{background:#f2f3fd}"
        ".page-break{page-break-after:always;border-top:1px dashed #cccff9;margin:28px 0}</style></head><body>",
    ]
    for el in model:
        kind = el[0]
        if kind == "title":
            parts.append(f"<h1>{_html_escape(el[1])}</h1>")
        elif kind == "heading":
            parts.append(f"<h{el[1]}>{_html_escape(el[2])}</h{el[1]}>")
        elif kind == "paragraph":
            if _s(el[1]):
                parts.append(f"<p>{_html_escape(el[1])}</p>")
        elif kind == "bullets":
            parts.append("<ul>" + "".join(f"<li>{_html_escape(i)}</li>" for i in el[1]) + "</ul>")
        elif kind == "kv":
            rows = "".join(f"<tr><th>{_html_escape(k)}</th><td>{_html_escape(v)}</td></tr>" for k, v in el[1])
            parts.append(f"<table>{rows}</table>")
        elif kind == "table":
            head, rows = el[1], el[2]
            thead = "".join(f"<th>{_html_escape(h)}</th>" for h in head)
            body = "".join(
                "<tr>" + "".join(f"<td>{_html_escape(c)}</td>" for c in row) + "</tr>" for row in rows
            )
            parts.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>")
        elif kind == "pagebreak":
            parts.append('<div class="page-break"></div>')
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


# --- PDF (reportlab) ------------------------------------------------------------


def _pdf_text(value) -> str:
    """reportlab's core fonts use WinAnsi (cp1252); replace anything outside it."""
    return _s(value).encode("cp1252", "replace").decode("cp1252")


def render_pdf(model: list[tuple], title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    heading_style = ParagraphStyle(
        "AiscHeading", parent=styles["Heading2"], textColor=colors.HexColor("#000079"),
    )
    title_style = ParagraphStyle(
        "AiscTitle", parent=styles["Title"], textColor=colors.HexColor("#000079"),
    )
    body = ParagraphStyle("AiscBody", parent=styles["BodyText"], fontSize=9.5, leading=13)

    def cell(value):
        return Paragraph(_pdf_text(value) or "", body)

    story = []
    for el in model:
        kind = el[0]
        if kind == "title":
            story.append(Paragraph(_pdf_text(el[1]), title_style))
            story.append(Spacer(1, 6 * mm))
        elif kind == "heading":
            size = {1: 16, 2: 13, 3: 11}.get(el[1], 12)
            story.append(Paragraph(_pdf_text(el[2]), ParagraphStyle(
                f"AiscH{el[1]}", parent=heading_style, fontSize=size, leading=size + 3,
            )))
            story.append(Spacer(1, 2 * mm))
        elif kind == "paragraph":
            if _s(el[1]):
                story.append(Paragraph(_pdf_text(el[1]), body))
                story.append(Spacer(1, 2 * mm))
        elif kind == "bullets":
            for item in el[1]:
                story.append(Paragraph("• " + _pdf_text(item), body))
        elif kind == "kv":
            data = [[cell(k), cell(v)] for k, v in el[1]]
            table = Table(data, colWidths=[45 * mm, None])
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccff9")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f3fd")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(table)
            story.append(Spacer(1, 4 * mm))
        elif kind == "table":
            head, rows = el[1], el[2]
            data = [[cell(h) for h in head]] + [[cell(c) for c in row] for row in rows]
            table = Table(data, repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccff9")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f3fd")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(table)
            story.append(Spacer(1, 4 * mm))
        elif kind == "pagebreak":
            story.append(PageBreak())
    if not story:
        story.append(Paragraph(_pdf_text(title), title_style))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=_pdf_text(title))
    doc.build(story)
    return buf.getvalue()


# --- DOCX (python-docx) ---------------------------------------------------------


def render_docx(model: list[tuple], title: str) -> bytes:
    import docx

    document = docx.Document()
    document.add_heading(_s(title), level=0)
    for el in model:
        kind = el[0]
        if kind == "title":
            document.add_heading(_s(el[1]), level=0)
        elif kind == "heading":
            document.add_heading(_s(el[2]), level=min(int(el[1]) + 1, 4))
        elif kind == "paragraph":
            if _s(el[1]):
                document.add_paragraph(_s(el[1]))
        elif kind == "bullets":
            for item in el[1]:
                document.add_paragraph(_s(item), style="List Bullet")
        elif kind == "kv":
            table = document.add_table(rows=0, cols=2)
            table.style = "Table Grid"
            for k, v in el[1]:
                cells = table.add_row().cells
                cells[0].text = _s(k)
                cells[1].text = _s(v)
        elif kind == "table":
            head, rows = el[1], el[2]
            table = document.add_table(rows=1, cols=len(head))
            table.style = "Table Grid"
            for i, h in enumerate(head):
                table.rows[0].cells[i].text = _s(h)
            for row in rows:
                cells = table.add_row().cells
                for i, c in enumerate(row[:len(head)]):
                    cells[i].text = _s(c)
        elif kind == "pagebreak":
            document.add_page_break()
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# --- entry point ----------------------------------------------------------------


def render_document(snapshot: dict, fmt: str) -> tuple[bytes, str]:
    """Render a snapshot to (bytes, content_type) for pdf/docx/html."""
    if fmt not in CONTENT_TYPES:
        raise ValueError(f"unsupported report format {fmt!r}")
    title = _project_name(snapshot)
    model = build_document(snapshot)
    if fmt == "html":
        return render_html(model, title), CONTENT_TYPES["html"]
    if fmt == "docx":
        return render_docx(model, title), CONTENT_TYPES["docx"]
    return render_pdf(model, title), CONTENT_TYPES["pdf"]
