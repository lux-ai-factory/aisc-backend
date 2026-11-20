"""Rendering a report snapshot.

One renderer: the in-process `aisc_report/report_renderer/` draws a real PDF, Word or HTML
document from the frozen snapshot (ADR 0008). There is no external renderer and no second
implementation — `generate` is the single seam the report router uses, so tests can stub it
(contract-first) without a renderer.

An HTML page is never returned for a pdf/docx request: the renderer produces the real document.
"""
from __future__ import annotations

from aisc_backend.utils.upstream import UpstreamServiceError
from aisc_report.services import report_render


class ReportRenderError(UpstreamServiceError):
    """The renderer could not produce the requested document."""


async def generate(snapshot: dict, fmt: str = "pdf") -> tuple[bytes, str]:
    """Render a snapshot to (document_bytes, content_type) with the in-process renderer."""
    if fmt not in report_render.FORMATS:
        fmt = "pdf"
    return await report_render.render_document(snapshot, fmt)
