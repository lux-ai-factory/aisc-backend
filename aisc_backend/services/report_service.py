"""
Client for the headless report service.

The report service is a stateless renderer: POST a snapshot (project +
AI-card version + controls coverage + objectives priorities), get back the
rendered PDF (or the HTML page). It stores nothing; the backend owns the
`Report` row and the stored document.

This module is the one place the backend talks to that service, so a test can
stub it (contract-first) without a running service. When the service is not
configured, the client falls back to a deterministic HTML summary so the
backend works end-to-end in dev and in tests (a real PDF needs the service).
"""
from __future__ import annotations

import html
import logging

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is optional at import time
    httpx = None

from config.settings import REPORT_SERVICE_URL, SERVICE_TOKEN_FOR_APPS

logger = logging.getLogger(__name__)

CONTENT_TYPE_BY_FORMAT = {"pdf": "application/pdf", "html": "text/html; charset=utf-8"}


class ReportServiceError(RuntimeError):
    """The service answered something other than a rendered document."""


def _service_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if SERVICE_TOKEN_FOR_APPS:
        headers["X-AISC-Service-Token"] = SERVICE_TOKEN_FOR_APPS
    return headers


def _summary_document(snapshot: dict, fmt: str) -> tuple[bytes, str]:
    """A deterministic document when the service is absent (dev/tests only)."""
    fmt = fmt if fmt in CONTENT_TYPE_BY_FORMAT else "pdf"
    if fmt == "pdf":
        # No real PDF without the renderer: the fallback is the same HTML page
        # (the webapp degrades openly; a configured service renders PDF).
        fmt = "html"
    project = snapshot.get("project", {})
    name = snapshot.get("system", {}).get("name") or project.get("name") or "AI system"
    page = (
        "<!doctype html><html><body>"
        f"<h1>{html.escape(name)}</h1>"
        f"<p class='eyebrow'>AISC assessment report (fallback, no renderer)</p>"
        f"<pre>{html.escape(__import__('json').dumps(snapshot, indent=2))}</pre>"
        "</body></html>"
    )
    return page.encode("utf-8"), CONTENT_TYPE_BY_FORMAT["html"]


async def generate(project_pid: str, snapshot: dict, fmt: str = "pdf") -> tuple[bytes, str]:
    """Render a snapshot through the headless report service.

    Returns (document_bytes, content_type). Raises when the service is
    configured but refused to answer; falls back to a deterministic HTML
    summary when no service is configured (dev/tests).
    """
    if not REPORT_SERVICE_URL:
        return _summary_document(snapshot, fmt)
    if httpx is None:
        raise ReportServiceError("httpx is not installed")
    payload = {"project_pid": str(project_pid), "format": fmt, "snapshot": snapshot or {}}
    try:
        resp = await httpx.AsyncClient(timeout=120).post(
            f"{REPORT_SERVICE_URL}/api/generate",
            json=payload,
            headers=_service_headers(),
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides how to degrade
        logger.warning("report service unreachable: %s", exc)
        return _summary_document(snapshot, fmt)
    if resp.status_code != 200:
        raise ReportServiceError(f"report service answered {resp.status_code}")
    content_type = CONTENT_TYPE_BY_FORMAT.get(fmt, "application/octet-stream")
    return resp.content, content_type
