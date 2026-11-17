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

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is optional at import time
    httpx = None

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


def render_locally(snapshot: dict, fmt: str) -> tuple[bytes, str]:
    """Render with the in-process renderer (real PDF/DOCX/HTML)."""
    return report_render.render_document(snapshot, fmt)


async def generate(project_pid: str, snapshot: dict, fmt: str = "pdf") -> tuple[bytes, str]:
    """Render a snapshot to (document_bytes, content_type).

    Uses the external headless renderer when `REPORT_SERVICE_URL` is configured;
    otherwise renders locally (a real PDF or DOCX). The external path degrades to
    the local renderer when the service is unreachable, so a report is always a
    real document.
    """
    if fmt not in report_render.FORMATS:
        fmt = "pdf"
    if not REPORT_SERVICE_URL:
        return render_locally(snapshot, fmt)
    if httpx is None:
        return render_locally(snapshot, fmt)
    payload = {"project_pid": str(project_pid), "format": fmt, "snapshot": snapshot or {}}
    try:
        resp = await httpx.AsyncClient(timeout=120).post(
            f"{REPORT_SERVICE_URL}/api/generate",
            json=payload,
            headers=_service_headers(),
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides how to degrade
        logger.warning("report service unreachable: %s", exc)
        return render_locally(snapshot, fmt)
    if resp.status_code != 200:
        raise ReportServiceError(f"report service answered {resp.status_code}")
    content_type = CONTENT_TYPE_BY_FORMAT.get(fmt, "application/octet-stream")
    return resp.content, content_type
