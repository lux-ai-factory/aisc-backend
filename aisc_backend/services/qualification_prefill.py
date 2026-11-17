"""
Qualification prefill from an uploaded document (pilot parity).

The pilot lets a person upload a PDF, a Word document or a plain text file and
have the questionnaire pre-filled from it by **text rules** — no model: the
document's lines are matched against each question's text, and the value found
is *proposed*. The person still accepts, edits or rejects every suggestion, so
this is a starting point, never a decision. Nothing here is stored.

The rules are deliberately plain and inspectable: a question matches a line that
starts with (or is) its label, and the value is what follows a colon on that
line or the next non-empty line. A label shorter than `MIN_LABEL` characters is
too generic to match anything, so it is skipped rather than guessed.
"""
from __future__ import annotations

import io
import re

#: Suffixes we can read, and how.
TEXT_SUFFIXES = (".txt", ".text", ".md", ".markdown", ".rst", ".csv", ".json")
WORD_SUFFIXES = (".docx",)
PDF_SUFFIXES = (".pdf",)

#: A label shorter than this is too generic to match a document line.
MIN_LABEL = 8


class UnsupportedDocument(ValueError):
    """The uploaded file is not one of the readable kinds."""


def _suffix(filename: str) -> str:
    name = (filename or "").rsplit("/", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def _docx_text(blob: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(blob))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(": ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def _pdf_text(blob: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(filename: str, blob: bytes) -> str:
    """The document's plain text, or `UnsupportedDocument`."""
    suffix = _suffix(filename)
    if suffix in TEXT_SUFFIXES:
        return blob.decode("utf-8", errors="replace")
    if suffix in WORD_SUFFIXES:
        return _docx_text(blob)
    if suffix in PDF_SUFFIXES:
        return _pdf_text(blob)
    raise UnsupportedDocument(
        f"{suffix or 'this file'} is not readable: upload a PDF, a Word document "
        "(.docx) or a plain text file"
    )


def _norm(text: str) -> str:
    text = re.sub(r"[^\w\s:/-]", " ", str(text or "").casefold())
    return re.sub(r"\s+", " ", text).strip(" :")


def _value_after_colon(line: str) -> str:
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def _label_re(label: str):
    """A regex matching the label on a line, tolerating its punctuation and spacing."""
    return re.compile(r"\W+".join(re.escape(word) for word in label.split()), re.I)


def _match(line: str, label: str) -> str | None:
    """How the line answers this question: `"follows"`, an inline value, or nothing.

    A line matches when it IS the label (its value follows) or when the label
    leads it and a separator (`:`/`=`) follows — so a line that merely begins
    with the question and carries on with other prose is not a value.
    """
    if _norm(line) == label:
        inline = _value_after_colon(line)
        return inline or "follows"
    match = _label_re(label).match(line.lstrip())
    if match is None:
        return None
    rest = line.lstrip()[match.end():].lstrip(" \t?!.,;")
    if not rest:
        return "follows"
    after = re.match(r"^[:=]\s*(.*)$", rest)
    if after is None:
        return None
    return after.group(1).strip() or "follows"


def propose(definition: dict, text: str) -> list[dict]:
    """The suggestions for this questionnaire's questions, in question order.

    Each suggestion is `{field, value, line, matched}`; a question with no match
    is left out (the caller reports the rest as unmatched).
    """
    questions = (definition or {}).get("questions")
    if not isinstance(questions, list):
        return []
    lines = [line.rstrip() for line in (text or "").splitlines()]
    out: list[dict] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        field = str(question.get("field") or "").strip()
        label = _norm(question.get("text") or "")
        if not field or len(label) < MIN_LABEL:
            continue
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            found = _match(line, label)
            if found is None:
                continue
            value = found
            if found == "follows":
                nxt = next(
                    (lines[i].strip() for i in range(index + 1, len(lines))
                     if lines[i].strip()),
                    "",
                )
                value = _value_after_colon(nxt) or nxt
            if value and value != "follows":
                out.append({
                    "field": field,
                    "value": value,
                    "line": index + 1,
                    "matched": question.get("text") or field,
                })
            break
    return out
