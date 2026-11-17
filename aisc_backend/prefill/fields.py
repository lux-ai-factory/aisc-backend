"""What a document says about each field of the form.

Two signals, both deterministic:

* **The Annex.** The form's fourteen questions are the points of EU AI Act
  Annex IV, and a technical documentation file is usually written against the
  same Annex. A heading of "Annex IV(1)(a)", or just "1(a)", names the question;
  what follows it, up to the next heading, is the answer.
* **Labels.** "System name:", "Provider:", "Intended purpose:" and the handful
  of ways people write those.

Neither needs a model, so this runs on every install.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import unicodedata

from aisc_backend.prefill.components import section_lines as component_lines
from aisc_backend.prefill.headings import ANNEX_HEADING, COMPONENTS_HEADING
from aisc_backend.prefill.headings import heading as heading_match
from aisc_backend.prefill.risks import RISKS_HEADING

#: The seven metadata fields on the form, and the labels people write for them.
#: Order matters only in that the first label to match wins for a field.
METADATA_LABELS: dict[str, tuple[str, ...]] = {
    "systemName": ("system name", "name of the system", "ai system name", "system", "name"),
    "systemVersion": ("system version", "version", "release"),
    "company": ("provider", "company", "organisation", "organization", "vendor", "supplier"),
    "description": ("description", "system description", "summary", "overview"),
    "targetUseCase": ("intended purpose", "purpose", "use case", "intended use", "target use case"),
    "targetUsers": ("intended users", "users", "target users", "end users"),
    "intendedDeployers": ("deployers", "intended deployers", "customers", "operators"),
}

#: Checked against the shared file, so a field the form has and this does
#: not know how to label is a failure here rather than a silent gap.
METADATA_FIELDS = tuple(METADATA_LABELS)

#: The Annex points the form asks about, and the field each one fills.
#:
#: Shared with the form as a file rather than written twice, the way the
#: VAIR lists are shared as vair_vocab.json: the form is TypeScript and this is
#: Python, and a mapping that lived in both would drift. Two of the points are
#: merged on the form, so either letter fills the same field.
_FIELDS_FILE = "prefill_fields.json"


def candidate_paths(module_file: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Where to look for the shared field mapping, best first.

    Beside the package is where the image puts it. The repo layout is a
    fallback for running the tests from a checkout, and it is built only when
    there are enough directories above to build it: in the image there are not,
    and asking for a missing parent by index raises at start.
    """
    here = (module_file or pathlib.Path(__file__)).resolve()
    places = []
    configured = os.environ.get("PREFILL_FIELDS_PATH", "").strip()
    if configured:
        places.append(pathlib.Path(configured))
    places.append(here.parent / _FIELDS_FILE)
    parents = here.parents
    if len(parents) > 3:
        places.append(parents[3] / "src" / "data" / "prefillFields.json")
    return places


def _load_fields() -> tuple[tuple[str, ...], dict[tuple[str, str], str]]:
    for candidate in candidate_paths():
        if candidate.is_file():
            loaded = json.loads(candidate.read_text())
            annex = {
                (point, letter): field
                for point, letters in loaded["annex"].items()
                for letter, field in letters.items()
            }
            return tuple(loaded["metadata"]), annex
    raise FileNotFoundError(
        f"{_FIELDS_FILE} is not beside this package and no PREFILL_FIELDS_PATH is set; "
        f"looked in {', '.join(str(p) for p in candidate_paths())}"
    )


_METADATA_ORDER, ANNEX_FIELDS = _load_fields()

#: A heading naming an Annex point (prefill.headings, where the risk reader finds it too).
_ANNEX_HEADING = ANNEX_HEADING

#: "Label: value", where the label is at the start of the line. Anchored, so
#: "we think the system name: is a detail" is not read as a field. The separator
#: is a colon, or a dash with space before it (and after it, or the line's end):
#: "System-level logging" is prose that starts with a label's word.
_LABELLED = re.compile(r"^\s*(?:[-*•]\s*)?([A-Za-z][A-Za-z /'()-]{2,40}?)(?:\s*:\s*|\s+[–-](?:\s+|$))(.*)$")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t:-–")


def _label_of(line: str) -> tuple[str, str] | None:
    """(label, value) for "Label: value", or for a line that is only a label.

    The second case is how a heading arrives from a Word document: the label is
    its own paragraph and the value is the paragraph under it.
    """
    found = _LABELLED.match(line)
    if found:
        return found.group(1).strip().lower(), found.group(2).strip()
    bare = re.sub(r"^\s*(?:[-*\u2022]\s*)?", "", line).strip().rstrip(":").lower()
    if bare and _field_for_label(bare):
        return bare, ""
    return None


def _field_for_label(label: str) -> str | None:
    for field, labels in METADATA_LABELS.items():
        if label in labels:
            return field
    return None


def metadata_from_text(text: str) -> dict[str, str]:
    """The labelled fields, whether the value is on the line or under it."""
    found: dict[str, str] = {}
    lines = text.splitlines()
    # A part's "Name:" and "Provider:" are the part's, not the system's.
    parts = component_lines(lines)
    for index, line in enumerate(lines):
        if index in parts:
            continue
        labelled = _label_of(line)
        if not labelled:
            continue
        label, value = labelled
        field = _field_for_label(label)
        if field is None or field in found:
            continue
        if not value:
            # "System name" with the value on the next line, which is how a
            # heading in a Word document arrives.
            value = next((line.strip() for line in lines[index + 1:index + 3] if line.strip()), "")
            following = _label_of(value)
            if following and _field_for_label(following[0]):
                value = ""  # the next line is another label, not this one's value
        value = _clean(value)
        if value:
            found[field] = value
    return found


def annex_sections(text: str) -> dict[str, str]:
    """The text under each Annex heading, keyed by the field it fills.

    A section runs to the next heading. Two headings that fill the same field
    (1(d) and 1(e)) are joined, in the order they appear.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if heading_match(RISKS_HEADING, line) or heading_match(COMPONENTS_HEADING, line):
            # The risk register is its own section, not the end of 2(h).
            current = None
            continue
        heading = heading_match(_ANNEX_HEADING, line)
        if heading:
            point, letter = heading.group(1), heading.group(2).lower()
            current = ANNEX_FIELDS.get((point, letter))
            continue
        if current is None:
            continue
        if line.strip():
            sections.setdefault(current, []).append(line.strip())
    return {field: _clean(" ".join(lines)) for field, lines in sections.items() if lines}


def proposals_from_text(text: str) -> dict[str, str]:
    """Everything this document proposes, by form field.

    A field with nothing to say is absent rather than empty: proposing nothing
    must never be able to erase what somebody typed.
    """
    found = {**metadata_from_text(text), **annex_sections(text)}
    return {field: value for field, value in found.items() if value}


# Custom forms: questions matched by their own wording.
#
# A form built or imported by the install asks its own questions. The document
# names one the way it names a metadata field: a line that is the question's
# text, its citation, or both, possibly as "Label: value". What follows, up to
# the next line that names something, is the answer. A question tagged with an
# Annex point is also answered by that point's heading, as the default form is.

_NORM_WRAPPERS = ("**", "__")
_NORM_HASHES = re.compile(r"^#+\s*")
_NORM_MARKER = re.compile(r"^(?:[-*]\s+|\u2022\s*)")
_NORM_NUMBER = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s")


def norm(s: str) -> str:
    """A line or a question, as a comparable string.

    NFKC and lowercase; leading #s, list markers, numbering and surrounding
    **/__ removed until nothing changes; no space around §; whitespace
    collapsed; no trailing ?, : or full stop.
    """
    s = unicodedata.normalize("NFKC", s).lower().strip()
    while True:
        before = s
        for wrapper in _NORM_WRAPPERS:
            if len(s) > 2 * len(wrapper) and s.startswith(wrapper) and s.endswith(wrapper):
                s = s[len(wrapper) : -len(wrapper)].strip()
        s = _NORM_HASHES.sub("", s).strip()
        s = _NORM_MARKER.sub("", s).strip()
        s = _NORM_NUMBER.sub("", s).strip()
        if s == before:
            break
    # collapsed first, so at most one space sits beside a §: "\s*§\s*" would be
    # retried at every position of a long blank run
    s = " ".join(s.split())
    s = re.sub(r" ?§ ?", "§", s)
    return s.rstrip("?:. ")


def _point_of(field: str) -> str:
    """"q:annex-1:1de" -> "1de": the Annex point a default-form field answers."""
    return field.rsplit(":", 1)[-1]


#: The 14 Annex IV point ids, in the order the default form asks them. Built
#: from the shared field mapping, so it cannot drift from src/data/annexPoints.json.
ANNEX_POINT_IDS: tuple[str, ...] = tuple(dict.fromkeys(_point_of(f) for f in ANNEX_FIELDS.values()))


def annex_sections_by_point(text: str) -> dict[str, tuple[str, int]]:
    """annex_sections keyed by Annex point id, with the line of each point's first heading.

    The same walk: the same headings, the risk heading ends a section, and both
    letters of a merged point (1(d) and 1(e)) are joined in document order.
    """
    sections: dict[str, list[str]] = {}
    first_line: dict[str, int] = {}
    current: str | None = None
    for index, line in enumerate(text.splitlines()):
        if heading_match(RISKS_HEADING, line) or heading_match(COMPONENTS_HEADING, line):
            current = None
            continue
        heading = heading_match(_ANNEX_HEADING, line)
        if heading:
            field = ANNEX_FIELDS.get((heading.group(1), heading.group(2).lower()))
            current = _point_of(field) if field else None
            if current is not None:
                first_line.setdefault(current, index)
            continue
        if current is None:
            continue
        if line.strip():
            sections.setdefault(current, []).append(line.strip())
    return {
        point: (_clean(" ".join(lines)), first_line[point])
        for point, lines in sections.items()
        if lines
    }


#: The default form's citations. They name a question only through its Annex
#: heading (annex_sections_by_point), never as a label of their own, so the
#: default form proposes exactly what the Annex headings do.
_ANNEX_CITATIONS = frozenset(
    norm(f"Annex IV({point})({letter})") for point, letter in ANNEX_FIELDS
) | frozenset({norm("Annex IV(1)(d)-(e)"), norm("Annex IV(1)(g)-(h)")})


def _names_for(question: dict) -> set[str]:
    """The normalised strings a line may be to name this question."""
    text = norm(str(question.get("text") or ""))
    citation = norm(str(question.get("citation") or ""))
    names = {text} if text else set()
    if len(citation) >= 3 and citation not in _ANNEX_CITATIONS:
        names.add(citation)
    if text and citation:
        names.add(f"{citation} {text}")
    return names


def _named(line: str, names: list[set[str]]) -> tuple[list[int], str] | None:
    """Which questions this line names, and the value on it, if any."""
    whole = norm(line)
    hits = [i for i, n in enumerate(names) if whole and whole in n]
    if hits:
        return hits, ""
    if ":" in line:
        label, value = line.split(":", 1)
        label = norm(label)
        hits = [i for i, n in enumerate(names) if label and label in n]
        if hits:
            return hits, value.strip()
    return None


def proposals_for_questions(text: str, questions: list[dict]) -> dict[str, str]:
    """What this document answers, for a form's own questions, by field.

    `questions` are {"field", "text", "citation", "annexPoint"}. Per field, the
    match that starts first in the document wins; on a tie, the Annex heading.
    A match with nothing under it claims nothing, and a field with nothing is
    absent rather than empty.
    """
    lines = text.splitlines()
    names = [_names_for(q) for q in questions]
    # field -> (first line, 1 for a naming match and 0 for an Annex match, answer)
    candidates: dict[str, list[tuple[int, int, str]]] = {}

    def stops(line: str) -> bool:
        return bool(
            _named(line, names)
            or heading_match(_ANNEX_HEADING, line)
            or _MARKDOWN_HEADING.match(line)
            or heading_match(RISKS_HEADING, line)
            or heading_match(COMPONENTS_HEADING, line)
        )

    for index, line in enumerate(lines):
        found = _named(line, names)
        if not found:
            continue
        hits, value = found
        body = [value] if value else []
        for following in lines[index + 1 :]:
            if stops(following):
                break
            if following.strip():
                body.append(following.strip())
        answer = _clean(" ".join(body))
        if not answer:
            continue
        for i in hits:
            candidates.setdefault(questions[i]["field"], []).append((index, 1, answer))

    by_point = annex_sections_by_point(text)
    for q in questions:
        point = q.get("annexPoint")
        if point and point in by_point:
            answer, index = by_point[point]
            candidates.setdefault(q["field"], []).append((index, 0, answer))

    return {field: min(found)[2] for field, found in candidates.items()}
