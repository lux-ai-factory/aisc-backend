"""
Reporting endpoints, under /projects/{pid}/reports/*.

The backend is the single door: it assembles the report snapshot from its own
DB (latest card-version ref + controls coverage + control-objectives
priorities), asks the headless report service to render it (PDF by default,
HTML on request), stores the document in the `Report` row and serves it back,
and deletes reports on request. Every write is audited with projectPid.
"""
import asyncio
import hashlib
import json
import logging
import re
import threading
import uuid

from asgiref.sync import sync_to_async
from django.http import HttpResponse
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import caller_of, role_or_404
from aisc_backend.models.evaluation import Evaluation
from aisc_backend.models.project import Project
from aisc_backend.models.report import ProjectReportSettings, Report
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_backend.schemas.report import (
    FONT_CHOICES,
    LOGO_MIMES,
    MARKING_CHOICES,
    ReportIn,
    ReportOut,
    ReportSettingsIn,
    ReportSettingsOut,
)
from aisc_backend.services import report_service, report_snapshot
from aisc_backend.services.report_snapshot import REPORT_SECTIONS

router = Router(tags=["reporting"])

logger = logging.getLogger(__name__)

project_repository = ProjectRepository()
system_repository = SystemRepository()

#: the report defaults, used when a project has no customisation
DEFAULT_PRIMARY_COLOR = "#000fdf"
DEFAULT_ACCENT_COLOR = "#ff007e"
DEFAULT_FONT = "inter"
DEFAULT_FONT_SIZE = 11
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONTS = frozenset(FONT_CHOICES)
_MARKINGS = frozenset(MARKING_CHOICES)
#: the logo the renderer may embed: at most 1 MB of base64 (the size cap)
LOGO_MAX_BASE64 = 1_400_000


def _colour(value: str, default: str) -> str:
    return value.lower() if isinstance(value, str) and _HEX.match(value) else default


def _logo(value) -> dict | None:
    """A validated logo ({mime, data_base64}) or None."""
    if not isinstance(value, dict):
        return None
    mime = value.get("mime")
    data = value.get("data_base64")
    if mime not in LOGO_MIMES or not isinstance(data, str) or not data:
        return None
    return {"mime": mime, "data_base64": data[:LOGO_MAX_BASE64]}


def _style(settings: ProjectReportSettings | None) -> dict:
    """The report's look, as the renderer takes it (inside the snapshot):
    the full contract — cover title/subtitle, colours, typeface, page
    texts, marking, document id and logo."""
    default = {
        "report_title": "", "subtitle": "",
        "primary_color": DEFAULT_PRIMARY_COLOR, "accent_color": DEFAULT_ACCENT_COLOR,
        "font": DEFAULT_FONT, "font_size_pt": DEFAULT_FONT_SIZE,
        "header_text": "", "footer_text": "",
        "marking": "none", "show_document_id": False, "logo": None,
    }
    if settings is None:
        return default
    size = settings.font_size_pt
    try:
        size = float(size)
    except (TypeError, ValueError):
        size = DEFAULT_FONT_SIZE
    return {
        "report_title": (settings.report_title or "")[:200],
        "subtitle": (settings.subtitle or "")[:300],
        "primary_color": _colour(settings.primary_color, DEFAULT_PRIMARY_COLOR),
        "accent_color": _colour(settings.accent_color, DEFAULT_ACCENT_COLOR),
        "font": settings.font if settings.font in _FONTS else DEFAULT_FONT,
        "font_size_pt": min(16, max(8, round(size, 1))),
        "header_text": (settings.header_text or "").strip()[:120],
        "footer_text": (settings.footer_text or "").strip()[:120],
        "marking": settings.marking if settings.marking in _MARKINGS else "none",
        "show_document_id": bool(settings.show_document_id),
        "logo": _logo(settings.logo),
    }


def _settings_out(settings: ProjectReportSettings) -> ReportSettingsOut:
    return ReportSettingsOut(
        pid=settings.pid,
        default_format=settings.default_format,
        default_sections=settings.default_sections or [],
        report_title=settings.report_title,
        subtitle=settings.subtitle,
        primary_color=settings.primary_color,
        accent_color=settings.accent_color,
        font=settings.font,
        font_size_pt=settings.font_size_pt,
        header_text=settings.header_text,
        footer_text=settings.footer_text,
        marking=settings.marking,
        show_document_id=settings.show_document_id,
        logo=settings.logo,
        updated_by=settings.updated_by,
    )


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


def _report_out(report: Report) -> ReportOut:
    snapshot = report.snapshot or {}
    return ReportOut(
        pid=report.pid,
        project_id=snapshot.get("project", {}).get("pid") or report.project_id,
        period=report.period,
        version=report.version,
        format=report.format,
        file_ref=report.file_ref,
        status=report.status,
        system_version=snapshot.get("system", {}).get("version"),
        controls=snapshot.get("controls", []),
        objectives=snapshot.get("objectives"),
        evaluations=snapshot.get("evaluations", []),
        created_at=report.created_at.isoformat(),
    )


async def _render_report(report_pid: str, snapshot: dict, fmt: str) -> None:
    """Render + store the document on a 'pending' report row, flipping it to
    'generated' (with the document) or 'failed'. Async so it can run in a
    worker thread (``_launch_worker``) or be awaited directly in tests."""
    try:
        project_pid = str(snapshot.get("project", {}).get("pid") or "")
        document, content_type = await report_service.generate(project_pid, snapshot, fmt)
    except Exception as exc:                     # noqa: BLE001 - never crash the worker thread
        logger.warning("report %s render failed: %s", report_pid, exc)
        await Report.objects.filter(pid=report_pid).aupdate(status="failed")
        return
    try:
        report = await Report.objects.select_related("project").aget(pid=report_pid)
    except Report.DoesNotExist:                  # deleted while rendering
        return
    actual_fmt = "html" if content_type.startswith("text/html") else report.format
    report.document = document
    report.format = actual_fmt
    report.status = "generated"
    report.file_ref = f"reports/{report.project.pid}/{report.pid}.{actual_fmt}"
    await report.asave(update_fields=["document", "format", "status", "file_ref"])


def _render_report_sync(report_pid: str, snapshot: dict, fmt: str) -> None:
    """Thread entry point: run ``_render_report`` on that thread's event loop."""
    asyncio.run(_render_report(report_pid, snapshot, fmt))


def _launch_worker(report_pid: str, snapshot: dict, fmt: str) -> None:
    """Run the render in the background so the endpoint answers immediately."""
    threading.Thread(
        target=_render_report_sync,
        args=(report_pid, snapshot, fmt),
        daemon=True,
    ).start()


@router.get("/{pid}/reports", response=list[ReportOut])
async def list_reports(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    return [
        _report_out(report)
        async for report in Report.objects.filter(project=project)
    ]


@router.post("/{pid}/reports/generate", response=ReportOut)
async def generate_report(request, pid: uuid.UUID, data: ReportIn):
    """Assemble the snapshot (scoped to the picked evaluations and sections,
    falling back to the project's report settings), render it through the
    headless service, store the report (document + snapshot) and audit."""
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    settings = await ProjectReportSettings.objects.filter(project=project).afirst()
    configured = (
        settings.default_format
        if settings and settings.default_format in ("pdf", "html")
        else "pdf"
    )
    fmt = data.format if data.format in ("pdf", "html") else configured
    sections = [s for s in data.sections if s in REPORT_SECTIONS] or (
        [s for s in (settings.default_sections or []) if s in REPORT_SECTIONS]
        if settings else []
    ) or list(REPORT_SECTIONS)

    evaluation_pids = []
    for epid in data.evaluation_pids or []:
        try:
            eqid = uuid.UUID(str(epid))
        except ValueError:
            raise HttpError(404, f"no such evaluation {epid!r}")
        exists = await Evaluation.objects.filter(project=project, pid=eqid).aexists()
        if not exists:
            raise HttpError(404, f"no such evaluation {epid!r}")
        evaluation_pids.append(str(eqid))

    snapshot = await report_snapshot.build_snapshot(
        project, evaluation_pids=evaluation_pids, sections=sections
    )
    # the report's own version within the project (the cover's "Version"): the
    # highest existing version + 1, so deleting a mid-version report never
    # reuses a number another (still present) report carries
    last_version = (
        await Report.objects.filter(project=project)
        .order_by("-version")
        .values_list("version", flat=True)
        .afirst()
    )
    next_version = (last_version or 0) + 1
    style = _style(settings)
    style["report_version"] = next_version
    # the document id line: a stable id + a fingerprint of the snapshot
    style["document_id"] = "AISC-" + str(pid).replace("-", "")[:12].upper()
    style["fingerprint"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in snapshot.items() if k != "style"},
            sort_keys=True, default=str,
        ).encode()
    ).hexdigest()[:12]
    snapshot["style"] = style

    system = await system_repository.get_or_create(project)
    version = await system_repository.latest(system)

    # The endpoint answers immediately: the row is created 'pending' and the
    # render runs in the background (_launch_worker), flipping it to
    # 'generated' (with the document) or 'failed'. The webapp polls the list.
    report = await Report.objects.acreate(
        project=project,
        system_version=version,
        period=data.period,
        version=next_version,
        format=fmt,
        snapshot=snapshot,
        document=None,
        status="pending",
        generated_by=caller.subject,
    )
    await sync_to_async(log_action)(
        request,
        action="generate",
        resource_type="report",
        resource_id=str(report.pid),
        metadata={
            "projectPid": str(pid),
            "period": data.period,
            "format": fmt,
            "sections": sections,
            "evaluations": evaluation_pids,
            "version": next_version,
            "systemVersion": (
                (snapshot.get("system") or {}).get("version") or {}
            ).get("number"),
        },
    )
    _launch_worker(str(report.pid), snapshot, fmt)
    return _report_out(report)


@router.get("/{pid}/report-settings", response=ReportSettingsOut)
async def get_report_settings(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    settings, _ = await ProjectReportSettings.objects.aget_or_create(
        project=project,
        defaults={"default_format": "pdf", "default_sections": [], "updated_by": ""},
    )
    return _settings_out(settings)


@router.put("/{pid}/report-settings", response=ReportSettingsOut)
async def set_report_settings(request, pid: uuid.UUID, data: ReportSettingsIn):
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    fmt = data.default_format if data.default_format in ("pdf", "html") else "pdf"
    sections = [s for s in data.default_sections if s in REPORT_SECTIONS]
    size = data.font_size_pt
    try:
        size = float(size)
    except (TypeError, ValueError):
        size = DEFAULT_FONT_SIZE
    settings, _ = await ProjectReportSettings.objects.aupdate_or_create(
        project=project,
        defaults={
            "default_format": fmt,
            "default_sections": sections,
            "report_title": data.report_title.strip()[:200],
            "subtitle": data.subtitle.strip()[:300],
            "primary_color": _colour(data.primary_color, DEFAULT_PRIMARY_COLOR),
            "accent_color": _colour(data.accent_color, DEFAULT_ACCENT_COLOR),
            "font": data.font if data.font in _FONTS else DEFAULT_FONT,
            "font_size_pt": min(16, max(8, round(size, 1))),
            "header_text": data.header_text.strip()[:120],
            "footer_text": data.footer_text.strip()[:120],
            "marking": data.marking if data.marking in _MARKINGS else "none",
            "show_document_id": bool(data.show_document_id),
            "logo": _logo(data.logo),
            "updated_by": caller.subject,
        },
    )
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="report_settings",
        resource_id=str(settings.pid),
        metadata={
            "projectPid": str(pid),
            "format": fmt,
            "sections": sections,
            "title": settings.report_title,
        },
    )
    return _settings_out(settings)


@router.delete("/{pid}/reports/{report_pid}", response={204: None})
async def delete_report(request, pid: uuid.UUID, report_pid: uuid.UUID):
    """Delete a generated report (the card's delete action)."""
    project = await _project_for(request, pid, needed="editor")
    report = await Report.objects.filter(project=project, pid=report_pid).afirst()
    if report is None:
        raise HttpError(404, "no such report")
    await sync_to_async(log_action)(
        request,
        action="delete",
        resource_type="report",
        resource_id=str(report_pid),
        metadata={"projectPid": str(pid)},
    )
    await report.adelete()
    return 204, None


@router.get("/{pid}/reports/{report_pid}", response=ReportOut)
async def get_report(request, pid: uuid.UUID, report_pid: uuid.UUID):
    project = await _project_for(request, pid)
    report = await Report.objects.filter(project=project, pid=report_pid).afirst()
    if report is None:
        raise HttpError(404, "no such report")
    return _report_out(report)


@router.get("/{pid}/reports/{report_pid}/pdf")
async def report_pdf(request, pid: uuid.UUID, report_pid: uuid.UUID):
    """Stream the stored report document (PDF by default, HTML for html-format
    reports)."""
    project = await _project_for(request, pid)
    report = await Report.objects.filter(project=project, pid=report_pid).afirst()
    if report is None:
        raise HttpError(404, "no such report")
    if report.document is None:
        raise HttpError(409, "this report has no rendered document")
    content_type = (
        "application/pdf" if report.format != "html" else "text/html; charset=utf-8"
    )
    filename = f"report-{report.file_ref.split('/')[-1]}"
    return HttpResponse(
        content=bytes(report.document),
        content_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
