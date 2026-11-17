"""
Report blocks and the five built-in layouts (the pilot composer's palette,
folded into this app).

A layout is an ordered list of blocks ``{instance_id, block_type, options}``.
This module is the single source of truth for the block palette the composer
offers, the options each block takes, validation, the editor's outline, and the
read-only built-in layouts. The built-ins are code (not rows) so every project
shares them and can only duplicate them.

The block type ids and their options mirror the pilot's renderer contract
(`apps/report-composer`, re-expressed): the composer stores the same shape and
`services.report_render` draws each block from the frozen snapshot.
"""
from __future__ import annotations

import uuid

MAX_BLOCKS = 50

#: The pilot's palette groups, in the order the composer shows them.
GROUPS = (
    "AI card",
    "Control objectives and checklists",
    "Tests run",
    "Results",
    "Summary",
    "Document",
    "Other",
)


def _opt(type_, default=None, enum=None, label=""):
    spec: dict = {"type": type_}
    if default is not None:
        spec["default"] = default
    if enum is not None:
        spec["enum"] = list(enum)
    if label:
        spec["label"] = label
    return spec


def _spec(type_id, label, group, options=None, default_options=None):
    return {
        "type_id": type_id,
        "label": label,
        "group": group,
        "options_schema": {"properties": options or {}},
        "default_options": default_options or {},
    }


#: The block palette: what a layout may hold, and the options each block takes.
BLOCK_TYPES: tuple[dict, ...] = (
    # --- AI card ---
    _spec(
        "ai_card", "AI card", "AI card",
        {
            "show_components": _opt("boolean", False, label="Show components"),
            "show_tags": _opt("boolean", False, label="Show tags"),
            "show_graph_stats": _opt("boolean", False, label="Show graph statistics"),
        },
        {"show_components": True, "show_tags": True, "show_graph_stats": True},
    ),
    _spec(
        "risk_classification", "Risk classification", "AI card",
        {
            "show_chains": _opt("boolean", False, label="Show risk chains"),
            "show_impact_areas": _opt("boolean", False, label="Show impact areas"),
        },
        {"show_chains": True, "show_impact_areas": False},
    ),
    # --- Control objectives and checklists ---
    _spec(
        "control_objectives", "Control objectives", "Control objectives and checklists",
        {
            "group_by": _opt("string", "objective", enum=("objective", "risk"), label="Group by"),
            "show_status": _opt("boolean", False, label="Show status"),
            "show_severity": _opt("boolean", False, label="Show severity"),
            "show_quotes": _opt("boolean", False, label="Show quotes"),
            "show_rationale": _opt("boolean", False, label="Show rationale"),
            "include_unrated": _opt("boolean", False, label="Include unrated"),
        },
        {"group_by": "objective", "show_status": True, "show_severity": True},
    ),
    _spec(
        "control_answers", "Checklist answers", "Control objectives and checklists",
        {
            "show_scores": _opt("boolean", False, label="Show scores"),
            "show_unanswered": _opt("boolean", False, label="Show unanswered"),
            "include_archived": _opt("boolean", False, label="Include archived"),
        },
        {"show_scores": True, "show_unanswered": False},
    ),
    # --- Tests run ---
    _spec(
        "test_runs", "Test runs", "Tests run",
        {"detail": _opt("string", "summary", enum=("summary", "full"), label="Detail")},
        {"detail": "summary"},
    ),
    # --- Results ---
    _spec(
        "test_results", "Test results", "Results",
        {"detail": _opt("string", "summary", enum=("summary", "full"), label="Detail")},
        {"detail": "summary"},
    ),
    _spec(
        "chart", "Chart", "Results",
        {"dataset": _opt("string", "coverage_status",
                         enum=("coverage_status", "checklist_scores"), label="Dataset")},
        {"dataset": "coverage_status"},
    ),
    _spec("dashboard_chart", "Dashboard chart", "Results", {}, {}),
    # --- Summary ---
    _spec(
        "key_figures", "Key figures", "Summary",
        {"show_tool_headlines": _opt("boolean", False, label="Show tool headlines")},
        {"show_tool_headlines": False},
    ),
    _spec(
        "summary_coverage", "Summary and coverage", "Summary",
        {"show_uncovered_only": _opt("boolean", False, label="Show uncovered only")},
        {"show_uncovered_only": False},
    ),
    _spec(
        "changes_since", "Changes since", "Summary",
        {
            "compare_to": _opt("string", "previous", enum=("previous", "none"), label="Compare with"),
            "show_unchanged": _opt("boolean", False, label="Show unchanged"),
        },
        {"compare_to": "previous", "show_unchanged": False},
    ),
    _spec("amendments", "Amendments", "Summary", {}, {}),
    # --- Document ---
    _spec("cover", "Cover", "Document", {}, {}),
    _spec(
        "chapter", "Chapter", "Document",
        {"title": _opt("string", "", label="Title")},
        {"title": "Chapter"},
    ),
    _spec(
        "free_text", "Free text", "Document",
        {
            "title": _opt("string", "", label="Title"),
            "text": _opt("string", "", label="Text"),
            "format": _opt("string", "markdown", enum=("markdown", "text"), label="Format"),
        },
        {"title": "Free text", "text": "Write this section.", "format": "markdown"},
    ),
    _spec(
        "appendix", "Appendix", "Document",
        {"title": _opt("string", "Appendix", label="Title")},
        {"title": "Appendix"},
    ),
)

_BY_ID = {t["type_id"]: t for t in BLOCK_TYPES}


def block_type(type_id: str) -> dict | None:
    return _BY_ID.get(type_id)


def palette() -> list[dict]:
    """The composer's palette: block types grouped, in the palette's own order."""
    out = []
    for group in GROUPS:
        types = [t for t in BLOCK_TYPES if t["group"] == group]
        if types:
            out.append({"group": group, "types": types})
    return out


def default_options(type_id: str) -> dict:
    spec = _BY_ID.get(type_id)
    return dict(spec["default_options"]) if spec else {}


def new_block(type_id: str) -> dict:
    """A new block of a known type, with its default options filled in."""
    return {
        "instance_id": str(uuid.uuid4()),
        "block_type": type_id,
        "options": default_options(type_id),
    }


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return isinstance(value, str)
    except (ValueError, TypeError, AttributeError):
        return False


def _problem(instance_id, code, pointer, message) -> dict:
    return {"instance_id": instance_id, "code": code, "pointer": pointer, "message": message}


def validate_blocks(blocks) -> list[dict]:
    """Problems of a layout, as [{instance_id, code, pointer, message}].

    Layout-level first (too many blocks, a second cover/appendix), then per
    block: a UUID instance id, a known block type, an object of options whose
    required keys are present and whose enum values are allowed.
    """
    if not isinstance(blocks, list):
        return [_problem(None, "invalid_layout", "", "blocks must be a list")]
    problems: list[dict] = []
    if len(blocks) > MAX_BLOCKS:
        problems.append(
            _problem(None, "too_many_blocks", "", f"a layout holds at most {MAX_BLOCKS} sections")
        )
    covers = [b for b in blocks if isinstance(b, dict) and b.get("block_type") == "cover"]
    if len(covers) > 1:
        problems.append(
            _problem(covers[1].get("instance_id"), "duplicate_cover", "", "a layout has one cover at most")
        )
    appendices = [b for b in blocks if isinstance(b, dict) and b.get("block_type") == "appendix"]
    if len(appendices) > 1:
        problems.append(
            _problem(appendices[1].get("instance_id"), "duplicate_appendix", "",
                     "a layout has one appendix at most")
        )

    seen: set = set()
    for b in blocks:
        if not isinstance(b, dict) or not _is_uuid(b.get("instance_id")):
            problems.append(
                _problem(b.get("instance_id") if isinstance(b, dict) else None,
                         "invalid_block", "/instance_id", "a block needs a UUID instance id")
            )
            continue
        iid = b["instance_id"]
        if iid in seen:
            problems.append(_problem(iid, "duplicate_instance_id", "/instance_id",
                                     "is used by another section"))
            continue
        seen.add(iid)
        spec = _BY_ID.get(b.get("block_type"))
        if spec is None:
            problems.append(_problem(iid, "unknown_block_type", "/block_type",
                                     f"the section type {b.get('block_type')!r} is not available"))
            continue
        options = b.get("options")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            problems.append(_problem(iid, "invalid_options", "", "options must be an object"))
            continue
        for name, prop in (spec["options_schema"]["properties"] or {}).items():
            value = options.get(name, prop.get("default"))
            if prop.get("enum") is not None and value not in prop["enum"]:
                problems.append(_problem(iid, "invalid_options", f"/{name}",
                                         "must be one of " + ", ".join(map(str, prop["enum"]))))
    return problems


def outline(blocks) -> list[dict]:
    """The editor's outline: one row per block with its number and flags.

    A chapter (and the appendix) numbers; the blocks after a chapter are its
    children. `number` is the printed section number or "" for an unnumbered
    row; `empty` marks a chapter with nothing under it.
    """
    rows: list[dict] = []
    chapter_no = 0
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        type_id = b.get("block_type")
        spec = _BY_ID.get(type_id)
        options = b.get("options") or {}
        label = spec["label"] if spec else str(type_id)
        if type_id in ("chapter", "free_text", "appendix") and options.get("title"):
            label = str(options["title"])
        number = ""
        kind = "content"
        if type_id == "cover":
            kind = "cover"
        elif type_id == "chapter":
            chapter_no += 1
            number = str(chapter_no)
            kind = "chapter"
        elif type_id == "appendix":
            appendix_seen = True
            kind = "appendix"
            number = "A"
        rows.append(
            {
                "instance_id": b.get("instance_id"),
                "block_type": type_id,
                "label": label,
                "number": number,
                "kind": kind,
            }
        )
    # mark empty chapters (a chapter with no following content before the next chapter/appendix)
    for i, row in enumerate(rows):
        if row["kind"] != "chapter":
            continue
        empty = True
        for nxt in rows[i + 1:]:
            if nxt["kind"] in ("chapter", "appendix"):
                break
            if nxt["kind"] != "cover":
                empty = False
                break
        row["empty"] = empty
    return rows


# --- built-in layouts (read-only, shared by every project) ----------------------

BUILTIN_PREFIX = "builtin-"


def _builtin(slug: str, name: str, description: str, show_index: bool, blocks: list[dict]) -> dict:
    return {
        "id": BUILTIN_PREFIX + slug,
        "name": name,
        "description": description,
        "built_in": True,
        "revision": 0,
        "show_index": show_index,
        "numbering": True,
        "blocks": [
            {
                "instance_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"aisc-builtin/{slug}/{i}")),
                "block_type": b["block_type"],
                "options": {**default_options(b["block_type"]), **(b.get("options") or {})},
            }
            for i, b in enumerate(blocks)
        ],
    }


#: The five built-in layouts, ported from the pilot's presets.
BUILTIN_LAYOUTS: tuple[dict, ...] = (
    _builtin(
        "summary", "Summary",
        "Where the system stands, on one page: the key figures, what has no evidence yet and what changed.",
        False,
        [
            {"block_type": "cover"},
            {"block_type": "key_figures", "options": {"figures": ["version", "risks", "objectives", "coverage", "tests"]}},
            {"block_type": "summary_coverage", "options": {"show_uncovered_only": True}},
            {"block_type": "changes_since", "options": {"compare_to": "previous", "show_unchanged": False}},
            {"block_type": "amendments"},
        ],
    ),
    _builtin(
        "management-overview", "Management overview",
        "What the system is, how risky it is and what is missing, for management.",
        True,
        [
            {"block_type": "cover"},
            {"block_type": "key_figures", "options": {"show_tool_headlines": True}},
            {"block_type": "ai_card", "options": {"show_components": False, "show_tags": False, "show_graph_stats": False}},
            {"block_type": "risk_classification", "options": {"show_chains": False, "show_impact_areas": True}},
            {"block_type": "chart", "options": {"dataset": "coverage_status"}},
            {"block_type": "summary_coverage", "options": {"show_uncovered_only": True}},
            {"block_type": "changes_since", "options": {"compare_to": "previous"}},
            {"block_type": "amendments"},
        ],
    ),
    _builtin(
        "assessment-report", "Assessment report",
        "What was assessed and what came out: the system, the control objectives and the tests, in chapters.",
        True,
        [
            {"block_type": "cover"},
            {"block_type": "key_figures"},
            {"block_type": "chapter", "options": {"title": "The AI system"}},
            {"block_type": "ai_card", "options": {"show_components": True}},
            {"block_type": "risk_classification", "options": {"show_chains": True}},
            {"block_type": "chapter", "options": {"title": "Control objectives"}},
            {"block_type": "control_objectives", "options": {"group_by": "objective", "show_status": True, "show_severity": True}},
            {"block_type": "summary_coverage", "options": {"show_uncovered_only": False}},
            {"block_type": "chapter", "options": {"title": "Tests"}},
            {"block_type": "test_runs", "options": {"detail": "summary"}},
            {"block_type": "test_results", "options": {"detail": "summary"}},
            {"block_type": "changes_since", "options": {"compare_to": "previous"}},
            {"block_type": "amendments"},
        ],
    ),
    _builtin(
        "eu-ai-act", "EU AI Act conformity",
        "Every claim traced to its evidence: scope, the system and its risks, the control objectives with "
        "their answers, the tests, the findings and the method.",
        True,
        [
            {"block_type": "cover"},
            {"block_type": "free_text", "options": {"title": "Scope", "text": "Write this section.", "format": "markdown"}},
            {"block_type": "key_figures"},
            {"block_type": "chapter", "options": {"title": "The AI system"}},
            {"block_type": "ai_card", "options": {"show_components": True, "show_tags": True}},
            {"block_type": "risk_classification", "options": {"show_chains": True, "show_impact_areas": True}},
            {"block_type": "chapter", "options": {"title": "Control objectives"}},
            {"block_type": "control_objectives", "options": {"show_rationale": True, "show_severity": True, "include_unrated": True}},
            {"block_type": "control_answers", "options": {"show_scores": True}},
            {"block_type": "summary_coverage"},
            {"block_type": "chapter", "options": {"title": "Evidence"}},
            {"block_type": "test_runs", "options": {"detail": "full"}},
            {"block_type": "test_results", "options": {"detail": "full"}},
            {"block_type": "chart", "options": {"dataset": "checklist_scores"}},
            {"block_type": "changes_since", "options": {"compare_to": "previous"}},
            {"block_type": "free_text", "options": {"title": "Findings", "text": "Write this section.", "format": "markdown"}},
            {"block_type": "appendix", "options": {"title": "Method"}},
            {"block_type": "free_text", "options": {"title": "Method", "text": "Write this section.", "format": "markdown"}},
            {"block_type": "amendments"},
        ],
    ),
    _builtin(
        "technical-dossier", "Technical dossier",
        "Everything, for auditors and engineers: every objective with its quotes, every answer, every run of "
        "every status, and what did not change.",
        True,
        [
            {"block_type": "cover"},
            {"block_type": "free_text", "options": {"title": "Scope", "text": "Write this section.", "format": "markdown"}},
            {"block_type": "key_figures", "options": {"show_tool_headlines": True}},
            {"block_type": "chapter", "options": {"title": "The AI system"}},
            {"block_type": "ai_card", "options": {"show_components": True, "show_tags": True}},
            {"block_type": "risk_classification", "options": {"show_chains": True, "show_impact_areas": True}},
            {"block_type": "chapter", "options": {"title": "Control objectives"}},
            {"block_type": "control_objectives", "options": {"group_by": "risk", "show_quotes": True, "show_rationale": True, "show_severity": True, "include_unrated": True}},
            {"block_type": "control_answers", "options": {"show_scores": True, "show_unanswered": True, "include_archived": True}},
            {"block_type": "summary_coverage"},
            {"block_type": "chart", "options": {"dataset": "coverage_status"}},
            {"block_type": "chapter", "options": {"title": "Evidence"}},
            {"block_type": "test_runs", "options": {"detail": "full"}},
            {"block_type": "test_results", "options": {"detail": "full"}},
            {"block_type": "appendix", "options": {"title": "Method"}},
            {"block_type": "amendments"},
        ],
    ),
)

_BUILTIN_BY_ID = {b["id"]: b for b in BUILTIN_LAYOUTS}


def builtin_layouts() -> list[dict]:
    return [dict(b) for b in BUILTIN_LAYOUTS]


def builtin_layout(layout_id: str) -> dict | None:
    layout = _BUILTIN_BY_ID.get(layout_id)
    return dict(layout) if layout else None


def builtin_name(layout_id: str) -> str | None:
    layout = _BUILTIN_BY_ID.get(layout_id)
    return layout["name"] if layout else None
