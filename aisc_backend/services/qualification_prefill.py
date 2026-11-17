"""
Qualification prefill from an uploaded document (the pilot's rules, verbatim).

The pilot lets a person upload a PDF, a Word document or a plain text file and
have the form pre-filled from it by **text rules** — no model. The rules are not
re-invented here: `aisc_backend/prefill/` is the pilot's own
`services/prefill/prefill` package (`aisc-qualification`, `pilot` branch),
vendored unchanged except for its import path, and this module is the one place
the app talks to it. Its own test suite is vendored beside it
(`tests/pilot_prefill/`) and runs against this copy, so "the rules are the
pilot's" is checked rather than asserted.

What the pilot's rules do, in one line each:

* `documents.read_document` — the text of a .txt/.md/.markdown/.text/.pdf/.docx,
  with the cp1252 fallback and the .docx expansion cap.
* `fields.proposals_for_questions` — a line that names one of the form's own
  questions (its wording, or its citation), with the answer running to the next
  line that names something.
* `fields.proposals_from_text` — the seven metadata fields ("System name:",
  "Provider:") and the Annex IV sections.
* `risks.risks_from_text` — one risk chain per row under a Risks heading, with
  its VAIR terms.
* `picks.picks_from_text` — the VAIR lists (system type, the four tag sets).
* `merge.merge` / `risks.merge_risks` — "empty" fills blanks only, "replace"
  takes the document wherever it has something, and a document that says nothing
  about a field **never** erases it.

This app's form stores far more than prose answers: the profile block, the tag
sets and the risk rows live inside the answers payload under `__blocks`. The
mapping from the pilot's field names onto that storage is `PROFILE_FIELDS` and
`TAG_FIELDS` below, and a document naming something this form has no field for
(system name, version, provider, the VAIR purpose) is reported in `unplaced`
rather than dropped in silence.
"""
from __future__ import annotations

import json
import os

from aisc_backend.prefill import documents, fields, picks, risks
from aisc_backend.prefill.documents import DocumentUnreadable
from aisc_backend.prefill.merge import UnknownMode, merge

#: The answers payload's blocks, as the webapp stores them (lib/qualification.ts).
BLOCKS_KEY = "__blocks"

#: The router's name for "this file cannot be read" (it answers 415).
UnsupportedDocument = DocumentUnreadable

#: The pilot reads a document a person can hand over, not a data set: 10 MiB.
MAX_BYTES = int(os.environ.get("PREFILL_MAX_BYTES", 10 * 1024 * 1024))


class UploadTooLarge(ValueError):
    """The upload is past MAX_BYTES, so it is never read."""


#: The pilot's metadata field -> this form's profile block key. Four of the
#: seven have a field here; see `unplaced` for the other three.
PROFILE_FIELDS = {
    "description": "description",
    "targetUseCase": "targetUseCase",
    "targetUsers": "targetUsers",
    "intendedDeployers": "intendedDeployers",
}

#: The pilot's VAIR pick -> this form's tag set. `purpose` and the two operator
#: terms have no tag set here; they are reported in `unplaced`.
TAG_FIELDS = {
    "systemType": "targetSystem",
    "sectorTags": "sector",
    "marketFormTags": "marketForm",
    "localityTags": "locality",
}


def extract_text(filename: str, blob: bytes) -> str:
    """The document's plain text, or `UnsupportedDocument`."""
    return documents.read_document(blob, filename)


def check_size(blob: bytes) -> None:
    """Refuse an upload past MAX_BYTES before anything reads it."""
    if len(blob or b"") > MAX_BYTES:
        raise UploadTooLarge(f"the file is larger than {MAX_BYTES} bytes")


def question_fields(definition: dict) -> list[dict]:
    """The form's questions in the shape the pilot's reader takes."""
    questions = (definition or {}).get("questions")
    if not isinstance(questions, list):
        return []
    out: list[dict] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        field = str(question.get("field") or "").strip()
        if not field:
            continue
        out.append({
            "field": field,
            "text": str(question.get("text") or ""),
            "citation": str(question.get("citation") or ""),
            "annexPoint": question.get("annexPoint") or None,
        })
    return out


def propose(definition: dict, text: str) -> dict[str, str]:
    """What the document answers, for the form's own questions, by field."""
    questions = question_fields(definition)
    if not questions:
        return {}
    return fields.proposals_for_questions(text, questions)


def read_blocks(current: dict | None) -> dict:
    """The form's blocks as they are now, from the answers' `__blocks` value.

    The webapp sends its answers as the form holds them, blocks included. A
    malformed or absent value means "no blocks yet", never a failure: the
    document is read either way and the blocks it fills are simply all blank.
    """
    raw = (current or {}).get(BLOCKS_KEY)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        raw = {}
    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
    rows = raw.get("risks") if isinstance(raw.get("risks"), list) else []
    return {
        "profile": {str(k): v for k, v in profile.items()},
        "tags": {str(k): v for k, v in tags.items()},
        "risks": [row for row in rows if isinstance(row, dict)],
    }


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


#: The pilot's risk row -> this form's row. The pilot's card asks no measure
#: against a risk (its reader recognises a document's "Control:" lines and skips
#: them), while this form has four measure fields, so those stay blank for the
#: person to fill in rather than being invented.
def our_risk_rows(rows) -> list[dict]:
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "affected": _text(row.get("affected")),
            "risk": _text(row.get("risk")),
            "source": _text(row.get("source")),
            "vulnerability": _text(row.get("vulnerability")),
            "consequence": _text(row.get("consequence")),
            "impactAreas": [str(area).strip() for area in (row.get("areas") or []) if str(area).strip()],
            "control": "",
            "followUpControl": "",
            "sourceTerm": _text(row.get("sourceTerm")),
            "consequenceTerm": _text(row.get("consequenceTerm")),
            "impactTerm": _text(row.get("impactTerm")),
            "controlTerm": "",
            "followUpControlTerm": "",
        })
    return out


def _tag_values(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _merge_tags(current: dict, proposed: dict, mode: str) -> tuple[dict, list[str]]:
    """The tag sets after the merge: the pilot's rule, on a list of terms.

    Under "empty" a set the form already has is kept; under "replace" the
    document's set takes its place — but only where the document named one, as
    with the text fields, so a document that lists no sectors empties none.
    """
    out = {str(k): _tag_values(v) for k, v in (current or {}).items()}
    filled: list[str] = []
    for key, names in (proposed or {}).items():
        if not names:
            continue
        if mode == "empty" and _tag_values(out.get(key)):
            continue
        out[key] = list(names)
        filled.append(key)
    return out, sorted(filled)


def prefill(definition: dict, text: str, current: dict | None = None,
            mode: str = "empty") -> dict:
    """The whole job: read the document, propose, merge, report.

    `current` is the form as it stands and `mode` the caller's choice of merge.
    Nothing is stored, and a field the document says nothing about keeps what it
    had.
    """
    if mode not in ("empty", "replace"):
        raise UnknownMode(f"a mode is one of empty, replace, not {mode!r}")

    blocks = read_blocks(current)
    # `__blocks` is not an answer: the merge works on the answers themselves.
    answers = {
        str(k): v
        for k, v in (current or {}).items()
        if k != BLOCKS_KEY and isinstance(v, str)
    }
    questions = question_fields(definition)
    proposed = propose(definition, text)
    merged = merge(answers, proposed, mode)

    metadata = fields.proposals_from_text(text)
    profile_proposed = {
        our_key: value
        for canonical, value in metadata.items()
        if (our_key := PROFILE_FIELDS.get(canonical))
    }
    profile = merge(
        {str(k): v for k, v in (blocks["profile"] or {}).items() if isinstance(v, str)},
        profile_proposed,
        mode,
    )

    found_picks = picks.picks_from_text(text)
    tag_proposed = {
        TAG_FIELDS[canonical]: list(names) if isinstance(names, list) else [names]
        for canonical, names in found_picks.items()
        if canonical in TAG_FIELDS
    }
    tags, tags_filled = _merge_tags(blocks["tags"], tag_proposed, mode)

    found_risks = risks.risks_from_text(text)
    risk_rows, risks_kept = risks.merge_risks(
        [{**row, "areas": row.get("impactAreas")} for row in blocks["risks"]],
        found_risks,
        mode,
    )

    unplaced = {k: v for k, v in metadata.items() if k not in PROFILE_FIELDS}
    unplaced.update({k: v for k, v in found_picks.items() if k not in TAG_FIELDS})

    answered = set(proposed) | set(profile_proposed)
    return {
        "read": True,
        "source": "document",
        # deterministic: no model proposed any of this
        "model": None,
        "values": merged.values,
        "filled": merged.filled,
        "kept": merged.kept,
        "proposed": sorted(proposed),
        "profile": profile.values,
        "profileFilled": profile.filled,
        "tags": tags,
        "tagsFilled": tags_filled,
        # rows to show, or None to leave the form's rows as they are
        "risks": None if risk_rows is None else our_risk_rows(risk_rows),
        "risksKept": risks_kept,
        "risksProposed": len(found_risks),
        "picks": found_picks,
        "picksProposed": len(found_picks),
        "unmatched": [q["field"] for q in questions if q["field"] not in answered],
        "unplaced": unplaced,
    }
