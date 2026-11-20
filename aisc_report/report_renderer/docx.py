"""The Word (docx) export: the same report sections the PDF draws, written with python-docx.

The fragment HTML of each block is walked with the standard library's parser (no browser, no
lxml): headings, paragraphs, list items, tables and data-URI images are turned into Word
elements; charts and inline SVG are skipped (their tables, when a block asks for one, carry
the numbers).
"""
from __future__ import annotations

import base64
import io
import re
from html.parser import HTMLParser

from docx import Document
from docx.shared import Inches, Pt

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_IMAGE_WIDTH_IN = 5.0


class _FragmentWriter(HTMLParser):
    """Turns one HTML fragment into paragraphs and tables of a python-docx document."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.doc = document
        self._text: list[str] = []
        self._heading: int | None = None
        self._bullets = 0
        self._table = None
        self._row = None
        self._col = 0
        self._cell_text: list[str] | None = None

    # -- text buffers --
    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        self._text = []
        if not text:
            return
        if self._cell_text is not None:
            self._cell_text.append(text)
        elif self._heading:
            self.doc.add_heading(text, level=self._heading)
            self._heading = None
        elif self._bullets:
            self.doc.add_paragraph(text, style="List Bullet")
        else:
            self.doc.add_paragraph(text)

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _HEADINGS:
            self._flush()
            self._heading = _HEADINGS[tag]
        elif tag in ("p", "br", "div", "section", "figure", "figcaption"):
            self._flush()
        elif tag == "li":
            self._flush()
            self._bullets += 1
        elif tag == "table":
            self._flush()
            self._table = self.doc.add_table(rows=0, cols=0)
            self._table.style = "Table Grid"
        elif tag == "tr" and self._table is not None:
            self._flush()
            self._row = self._table.add_row()
            self._col = 0
        elif tag in ("td", "th") and self._row is not None:
            self._flush()
            self._cell_text = []
        elif tag == "img":
            self._image(dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADINGS:
            self._flush()
            self._heading = None
        elif tag == "li":
            self._flush()
            self._bullets = max(0, self._bullets - 1)
        elif tag in ("td", "th") and self._row is not None:
            self._flush()
            text = " ".join(self._cell_text or [])
            if self._col < len(self._row.cells):
                self._row.cells[self._col].text = text
            self._col += 1
            self._cell_text = None
        elif tag in ("p", "div", "section", "figure", "figcaption"):
            self._flush()
        elif tag == "tr":
            self._row = None
            self._col = 0
            self._cell_text = None
        elif tag == "table":
            self._flush()
            self._table = None

    def close(self) -> None:                                            # noqa: D102 (HTMLParser)
        super().close()
        self._flush()

    def _image(self, attrs: dict) -> None:
        src = attrs.get("src") or ""
        if not src.startswith("data:") or ";base64," not in src:
            return
        try:
            payload = base64.b64decode(src.split(";base64,", 1)[1])
            self.doc.add_picture(io.BytesIO(payload), width=Inches(_IMAGE_WIDTH_IN))
        except Exception:                                                # noqa: BLE001 (an unreadable image)
            pass


def sections_to_docx(snapshot: dict, sections: list[dict]) -> bytes:
    """The report's sections as a Word document."""
    document = Document()
    style = document.styles["Normal"]
    style.font.size = Pt(10)
    project = (snapshot.get("project") or {}).get("name") or ""
    document.add_heading(f"{project} — Report".strip(" —"), level=0)
    for section in sections:
        if section.get("title") and section.get("kind") != "cover":
            document.add_heading(section["title"], level=1)
        writer = _FragmentWriter(document)
        writer.feed(section.get("html") or "")
        writer.close()
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
