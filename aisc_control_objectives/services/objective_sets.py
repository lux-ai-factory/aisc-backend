"""Comparing two versions of a custom objective set (pilot parity).

A set is edited as a draft and published as fixed, numbered versions
(`docs/appendices/2-control-objectives.md`); a person changing the draft needs to
see what they are about to change the set into. This module is the pure decision
behind that view: rows in, per-field differences out, no database, no request.

Two versions are compared **by `objective_id`**: it is the row's identity inside
a set (the draft refuses a duplicate), so a row whose code is unchanged but whose
text was reworded is a *change*, not a removal plus an addition — which is what a
reviewer reads it as.
"""
from __future__ import annotations

#: the row's fields, in the order the catalogue's own columns are shown
FIELDS = (
    "macro_requirement",
    "text",
    "legal_basis",
    "sub_requirement_label",
    "assessment_mode",
    "target",
    "standards_grounding",
    "grounding_tier_flag",
    "notes",
)


def _by_code(rows: list[dict]) -> dict[str, dict]:
    """Rows keyed by their code, the later row winning a duplicate (defensively)."""
    keyed: dict[str, dict] = {}
    for row in rows or []:
        code = str((row or {}).get("objective_id") or "").strip()
        if code:
            keyed[code] = row
    return keyed


def _normalise(value) -> str:
    """Fields are text on the wire; a missing one reads the same as an empty one."""
    return "" if value is None else str(value)


def diff_rows(before: list[dict], after: list[dict]) -> dict:
    """What happened between two versions of a set.

    `added`/`removed` are codes; `changed` names the fields that differ, with both
    values, so a reviewer reads the change itself rather than a boolean. Codes are
    reported in order of the later version, then the earlier one, so a stable diff
    reads the same way twice.
    """
    old, new = _by_code(before), _by_code(after)
    added = [code for code in new if code not in old]
    removed = [code for code in old if code not in new]
    changed = []
    for code, row in new.items():
        if code not in old:
            continue
        fields = [
            {
                "field": field,
                "before": _normalise((old[code] or {}).get(field)),
                "after": _normalise((row or {}).get(field)),
            }
            for field in FIELDS
            if _normalise((old[code] or {}).get(field)) != _normalise((row or {}).get(field))
        ]
        if fields:
            changed.append({"objective_id": code, "fields": fields})
    unchanged = [
        code for code in new if code in old and code not in {c["objective_id"] for c in changed}
    ]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }
