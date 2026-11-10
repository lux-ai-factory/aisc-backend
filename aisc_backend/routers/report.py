"""
Reporting endpoints, under /projects/{pid}/reports/*.

The backend is the single door: it assembles the report snapshot from its own
DB (latest card-version ref + controls coverage + control-objectives
priorities), asks the headless report service to render it (PDF by default,
HTML on request), stores the document in the `Report` row and serves it back.
Every write is audited with projectPid.
"""
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
from aisc_backend.schemas.report import ReportIn, ReportOut, ReportSettingsIn, ReportSettingsOut
from aisc_backend.services import report_service, report_snapshot
from aisc_backend.services.report_snapshot import REPORT_SECTIONS

router = Router(tags=["reporting"])

project_repository = ProjectRepository()
system_repository = SystemRepository()


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
        format=report.format,
        file_ref=report.file_ref,
        status=report.status,
        system_version=snapshot.get("system", {}).get("version"),
        controls=snapshot.get("controls", []),
        objectives=snapshot.get("objectives"),
        created_at=report.created_at.isoformat(),
    )


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
    document, _ = await report_service.generate(str(pid), snapshot, fmt)

    system = await system_repository.get_or_create(project)
    version = await system_repository.latest(system)

    report = await Report.objects.acreate(
        project=project,
        system_version=version,
        period=data.period,
        format=fmt,
        snapshot=snapshot,
        document=document,
        status="generated",
        generated_by=caller.subject,
    )
    report.file_ref = f"reports/{project.pid}/{report.pid}.{fmt}"
    await report.asave(update_fields=["file_ref"])

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
            "systemVersion": (
                (snapshot.get("system") or {}).get("version") or {}
            ).get("number"),
        },
    )
    return _report_out(report)


@router.get("/{pid}/report-settings", response=ReportSettingsOut)
async def get_report_settings(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    settings, _ = await ProjectReportSettings.objects.aget_or_create(
        project=project,
        defaults={"default_format": "pdf", "default_sections": [], "updated_by": ""},
    )
    return ReportSettingsOut(
        pid=settings.pid,
        default_format=settings.default_format,
        default_sections=settings.default_sections or [],
        updated_by=settings.updated_by,
    )


@router.put("/{pid}/report-settings", response=ReportSettingsOut)
async def set_report_settings(request, pid: uuid.UUID, data: ReportSettingsIn):
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    fmt = data.default_format if data.default_format in ("pdf", "html") else "pdf"
    sections = [s for s in data.default_sections if s in REPORT_SECTIONS]
    settings, _ = await ProjectReportSettings.objects.aupdate_or_create(
        project=project,
        defaults={
            "default_format": fmt,
            "default_sections": sections,
            "updated_by": caller.subject,
        },
    )
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="report_settings",
        resource_id=str(settings.pid),
        metadata={"projectPid": str(pid), "format": fmt, "sections": sections},
    )
    return ReportSettingsOut(
        pid=settings.pid,
        default_format=settings.default_format,
        default_sections=settings.default_sections or [],
        updated_by=settings.updated_by,
    )


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
