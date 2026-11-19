"""
Rendering a report snapshot.

Two renderers, one entry point:

- the **local** renderer (`services.report_render`) draws a real PDF or DOCX in
  this process from the frozen snapshot — the default, and the only path that
  runs when no external service is configured; and
- an **external** headless renderer, used only when `REPORT_SERVICE_URL` is set,
  kept for deployments that run one.

This module is the one place the backend talks to an external service, so a test
can stub `generate` (contract-first) without a running service. An HTML page is
never returned for a pdf/docx request: when the external service is absent the
local renderer produces the real document.
"""

from __future__ import annotations

import logging

import httpx
from config.settings import REPORT_SERVICE_URL, SERVICE_TOKEN_FOR_APPS

from aisc_backend.services import report_render

logger = logging.getLogger(__name__)

CONTENT_TYPE_BY_FORMAT = report_render.CONTENT_TYPES


class ReportServiceError(RuntimeError):
    """The service answered something other than a rendered document."""


def _service_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if SERVICE_TOKEN_FOR_APPS:
        headers["X-AISC-Service-Token"] = SERVICE_TOKEN_FOR_APPS
    return headers


async def render_locally(snapshot: dict, fmt: str) -> tuple[bytes, str]:
    """Render with the in-process renderer (real PDF/DOCX/HTML)."""
    return await report_render.render_document(snapshot, fmt)


async def generate(project_pid: str, snapshot: dict, fmt: str = "pdf") -> tuple[bytes, str]:
    """Render a snapshot to (document_bytes, content_type).

    Uses the external headless renderer when `REPORT_SERVICE_URL` is configured;
    otherwise renders locally (a real PDF or DOCX). A configured-but-unreachable
    service is an error — the worker marks the report failed rather than silently
    substituting a different renderer.
    """
    if fmt not in report_render.FORMATS:
        fmt = "pdf"
    if not REPORT_SERVICE_URL:
        return await render_locally(snapshot, fmt)
    if httpx is None:
        return await render_locally(snapshot, fmt)
    payload = {"project_pid": str(project_pid), "format": fmt, "snapshot": snapshot or {}}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{REPORT_SERVICE_URL.rstrip('/')}/api/generate",
                json=payload,
                headers=_service_headers(),
            )
    except httpx.HTTPError as exc:
        logger.warning("report service unreachable: %s", exc)
        raise ReportServiceError("report service unavailable") from exc
    if resp.status_code != 200:
        raise ReportServiceError(f"report service answered {resp.status_code}")
    content_type = CONTENT_TYPE_BY_FORMAT.get(fmt, "application/octet-stream")
    actual_type = resp.headers.get("content-type", "").split(";", 1)[0].strip()
    if actual_type != content_type.split(";", 1)[0] or not resp.content:
        raise ReportServiceError("report service returned an invalid document")
    if fmt == "pdf" and not resp.content.startswith(b"%PDF-"):
        raise ReportServiceError("report service returned an invalid PDF")
    return resp.content, content_type
