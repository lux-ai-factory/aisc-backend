"""The report renderer: the pilot's Jinja/WeasyPrint renderer, on the frozen snapshot.

REP-02: a real PDF (WeasyPrint), a real Word document (python-docx) and an HTML page, drawn
from the pilot's vendored templates and CSS (`aisc_report/report_renderer/`). No browser and
no Chromium: WeasyPrint is a Python library that draws CSS itself, and cairosvg draws SVG
(logos, charts) for the Word export. Every value comes from the snapshot, so rendering a
published report again gives the same document.

This module is the seam the rest of the app uses; the renderer itself lives in
`aisc_report/report_renderer/`.
"""
from __future__ import annotations

from aisc_report.report_renderer import document

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html; charset=utf-8",
}

FORMATS = ("pdf", "docx", "html")


async def render_document(snapshot: dict, fmt: str) -> tuple[bytes, str]:
    """The report document in `fmt` and its content type. Raises ValueError on an unknown format."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown report format {fmt!r}")
    style = document.style_of(snapshot)
    if fmt == "pdf":
        return await document.render_pdf(snapshot, style=style), CONTENT_TYPES["pdf"]
    if fmt == "docx":
        return await document.render_docx(snapshot, style=style), CONTENT_TYPES["docx"]
    html = await document.render_html(snapshot, style=style)
    return html.encode("utf-8"), CONTENT_TYPES["html"]
