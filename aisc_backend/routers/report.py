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
from time import perf_counter

from asgiref.sync import sync_to_async
from django.http import HttpResponse
from ninja import Router, UploadedFile, File
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
from aisc_backend.schemas.dashboard import DashboardIn
from aisc_backend.services import report_service, report_snapshot, report_storage
from aisc_backend.models.report_storage import ProjectDashboard, ReportArtifact
from django.db.models import F
from django.db import transaction
from django.conf import settings
from django.utils import timezone
import hmac
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


@router.post("/{pid}/reports/prepare", response=ReportOut)
async def prepare_report(request, pid: uuid.UUID, data: ReportIn):
    project = await _project_for(request, pid, needed="editor")
    report = await Report.objects.acreate(
        project=project, period=data.period, format=data.format,
        status="preparing", generated_by=caller_of(request).subject,
        snapshot={"project": {"pid": str(project.pid)}},
    )
    return _report_out(report)


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
        eqid = str(epid)
        exists = await Evaluation.objects.filter(project=project, pid=eqid).aexists()
        if not exists:
            raise HttpError(404, f"no such evaluation {eqid!r}")
        evaluation_pids.append(eqid)

    if data.report_pid:
        report = await Report.objects.filter(project=project, pid=data.report_pid).afirst()
        if report is None:
            raise HttpError(404, "No such report")
        if report.status != "preparing":
            raise HttpError(409, "Report already submitted")
    else:
        report = await Report.objects.acreate(
            project=project, period=data.period, format=fmt,
            status="preparing", generated_by=caller.subject,
        )
    snapshot_started = perf_counter()
    snapshot = await report_snapshot.build_snapshot(
        project,
        evaluation_pids=evaluation_pids,
        sections=sections,
        # charts carry report-owned artifact references (no image bytes)
        dashboard_widgets=[w.model_dump() for w in data.dashboard],
        evaluation_charts=[
            chart.model_dump(mode="json") for chart in data.evaluation_charts
        ],
    )
    snapshot = await sync_to_async(report_storage.store_images)(report, snapshot)
    logger.info(
        "Report %s snapshot assembled in %.3fs", report.pid, perf_counter() - snapshot_started,
    )
    # the report's own version within the project (the cover's "Version"): the
    # highest existing version + 1, so deleting a mid-version report never
    # reuses a number another (still present) report carries
    last_version = (
        await Report.objects.filter(project=project)
        .exclude(pk=report.pk).exclude(status="preparing")
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
    claimed = await Report.objects.filter(pk=report.pk, status="preparing").aupdate(
        system_version=version, period=data.period, version=next_version,
        format=fmt, snapshot=snapshot, status="pending",
        generation_started_at=timezone.now(),
    )
    if not claimed:
        raise HttpError(409, "Report already submitted")
    await report.arefresh_from_db()
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
    await sync_to_async(report_storage.delete_files)(report)
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


@router.get("/{pid}/dashboard")
async def get_dashboard(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    role = await role_or_404(request, project)
    dashboard = await ProjectDashboard.objects.filter(project=project).afirst()
    return {
        "widgets": dashboard.state.get("widgets", []) if dashboard else [],
        "layouts": dashboard.state.get("layouts", {}) if dashboard else {},
        "revision": dashboard.revision if dashboard else 0,
        "can_edit": role in ("editor", "owner"),
    }


@router.put("/{pid}/dashboard")
async def put_dashboard(request, pid: uuid.UUID, data: DashboardIn):
    project = await _project_for(request, pid, needed="editor")
    dashboard, _ = await ProjectDashboard.objects.aget_or_create(project=project)
    saved = await ProjectDashboard.objects.filter(pk=dashboard.pk, revision=data.revision).aupdate(
        state={
            "widgets": [w.model_dump(exclude_unset=True) for w in data.widgets],
            "layouts": {bp: [item.model_dump(exclude_unset=True) for item in items]
                        for bp, items in data.layouts.items()},
        },
        revision=F("revision") + 1,
    )
    if not saved:
        raise HttpError(409, "Dashboard changed; reload before saving")
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="dashboard",
        resource_id=str(project.pid),
        metadata={
            "projectPid": str(pid),
            "widgets": len(data.widgets),
            "revision": data.revision,
        },
    )
    return {
        **data.model_dump(exclude_unset=True),
        "revision": data.revision + 1,
        "can_edit": True,
    }


@router.post("/{pid}/reports/{report_pid}/artifacts")
async def upload_report_artifact(
    request, pid: uuid.UUID, report_pid: uuid.UUID, file: UploadedFile = File(...),
):
    project = await _project_for(request, pid, needed="editor")
    max_artifacts = getattr(settings, "REPORT_MAX_ARTIFACTS", 100)

    def reserve():
        # Transactional capacity reservation: even concurrent uploads can never
        # overshoot REPORT_MAX_ARTIFACTS (the counter is claimed under the row
        # lock and rolled back on rejection). The storage upload itself runs
        # outside the lock; a failed upload releases the reservation below.
        with transaction.atomic():
            try:
                report = Report.objects.select_for_update().get(
                    project=project, pid=report_pid, status="preparing",
                )
            except Report.DoesNotExist:
                raise HttpError(404, "No preparing report")
            if report.reserved_slots >= max_artifacts:
                raise HttpError(413, "Report chart limit reached")
            Report.objects.filter(pk=report.pk).update(
                reserved_slots=F("reserved_slots") + 1
            )
            report.reserved_slots += 1
            return report

    report = await sync_to_async(reserve)()
    try:
        artifact = await sync_to_async(report_storage.upload)(report, file)
    except Exception:
        # an upload that never stores (invalid PNG, storage failure) frees its slot
        await Report.objects.filter(pk=report.pk).aupdate(
            reserved_slots=F("reserved_slots") - 1
        )
        raise
    await sync_to_async(log_action)(
        request,
        action="upload",
        resource_type="report_artifact",
        resource_id=str(artifact.pid),
        metadata={"projectPid": str(pid), "report": str(report.pid), "bytes": file.size},
    )
    return {"pid": str(artifact.pid)}


@router.get("/{pid}/report-artifacts/{artifact_pid}", auth=None)
async def report_artifact(request, pid: uuid.UUID, artifact_pid: uuid.UUID):
    token = settings.SERVICE_TOKEN_FOR_APPS
    given = request.headers.get("X-AISC-Service-Token", "")
    if not token or not hmac.compare_digest(token, given):
        raise HttpError(401, "Unauthorized")
    artifact = await ReportArtifact.objects.filter(
        report__project__pid=pid, pid=artifact_pid,
    ).afirst()
    if artifact is None:
        raise HttpError(404, "No such artifact")
    from aisc_backend.repositories import file_repository
    response = await file_repository.get_s3_file_stream(artifact.storage_container, artifact.data)
    response["Content-Type"] = "image/png"
    return response
