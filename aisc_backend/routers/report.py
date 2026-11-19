"""
Reporting endpoints, under /projects/{pid}/*.

The backend is the single door: it owns report layouts (ordered blocks), the
project's report settings, and every generated `Report`. It assembles the
report snapshot from its own DB at generation time (the selected AI-card
version, the evidence map's coverage, controls, control-objectives priorities,
the picked evaluations and the layout's blocks), freezes it on the `Report` row
(with a checksum) and renders a real PDF or Word document through
`services.report_render`. Reads are served from the frozen snapshot, so a
published report does not change when the project does.

Every write is audited with projectPid.
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
from aisc_backend.auth.keycloak import KeycloakAuth
from aisc_backend.flags import require_flag
from aisc_backend.models.evaluation import Evaluation
from aisc_backend.models.project import Project
from aisc_backend.models.system import SystemVersion
from aisc_report.models import ProjectReportSettings, Report, ReportLayout
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.report import (
    FONT_CHOICES,
    LOGO_MIMES,
    MARKING_CHOICES,
    LayoutDuplicateIn,
    LayoutImportIn,
    LayoutPreviewIn,
    ReportIn,
    ReportLayoutIn,
    ReportLayoutOut,
    ReportOut,
    ReportSettingsIn,
    ReportSettingsOut,
)
from aisc_backend.services import report_blocks, report_local_runs, report_render, report_service, report_snapshot
from aisc_backend.services.report_snapshot import REPORT_SECTIONS

router = Router(tags=["reporting"], auth=[require_flag("reporting"), KeycloakAuth()])

logger = logging.getLogger(__name__)

project_repository = ProjectRepository()

FORMATS = ("pdf", "docx", "html")
SETTINGS_FORMATS = ("pdf", "docx", "html")

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


def _checksum_json(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checksum_bytes(value) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def _layout_out(layout) -> ReportLayoutOut:
    """A project layout row as the API answers it."""
    return ReportLayoutOut(
        id=str(layout.pid),
        name=layout.name,
        description=layout.description or "",
        blocks=layout.blocks or [],
        show_index=layout.show_index,
        numbering=layout.numbering,
        revision=layout.revision,
        built_in=False,
        created_at=layout.created_at.isoformat(),
        updated_at=layout.updated_at.isoformat(),
    )


def _builtin_out(layout: dict) -> ReportLayoutOut:
    return ReportLayoutOut(
        id=layout["id"],
        name=layout["name"],
        description=layout.get("description", ""),
        blocks=layout.get("blocks", []),
        show_index=layout.get("show_index", True),
        numbering=layout.get("numbering", True),
        revision=0,
        built_in=True,
    )


async def _resolve_layout(project: Project, layout_id: str) -> dict | None:
    """A layout (built-in or the project's own) as a plain dict, or None."""
    if not layout_id:
        return None
    builtin = report_blocks.builtin_layout(layout_id)
    if builtin is not None:
        return builtin
    try:
        row_pid = uuid.UUID(str(layout_id))
    except (ValueError, TypeError):
        return None
    row = await ReportLayout.objects.filter(project=project, pid=row_pid).afirst()
    if row is None:
        return None
    return {
        "id": str(row.pid),
        "name": row.name,
        "description": row.description or "",
        "blocks": row.blocks or [],
        "show_index": row.show_index,
        "numbering": row.numbering,
        "revision": row.revision,
        "built_in": False,
        "row": row,
    }


def _report_out(report: Report) -> ReportOut:
    snapshot = report.snapshot or {}
    layout = snapshot.get("layout") or {}
    return ReportOut(
        pid=report.pid,
        project_id=snapshot.get("project", {}).get("pid") or report.project_id,
        period=report.period,
        version=report.version,
        format=report.format,
        file_ref=report.file_ref,
        status=report.status,
        system_version=snapshot.get("system", {}).get("version"),
        layout_id=layout.get("id"),
        layout_revision=layout.get("revision"),
        controls=snapshot.get("controls", []),
        objectives=snapshot.get("objectives"),
        snapshot_checksum=report.snapshot_checksum or "",
        document_checksum=report.document_checksum or "",
        evaluations=snapshot.get("evaluations", []),
        created_at=report.created_at.isoformat(),
    )


# --- layouts -------------------------------------------------------------------


@router.get("/{pid}/report-block-types", response=dict)
async def report_block_types(request, pid: uuid.UUID):
    """The composer's palette: block types grouped, each with its options."""
    await _project_for(request, pid)
    return {"groups": report_blocks.palette(), "max_blocks": report_blocks.MAX_BLOCKS}


@router.get("/{pid}/report-layouts", response=list[ReportLayoutOut])
async def list_report_layouts(request, pid: uuid.UUID):
    """The built-in layouts (shared, read-only) then the project's own."""
    project = await _project_for(request, pid)
    out = [_builtin_out(b) for b in report_blocks.builtin_layouts()]
    out.extend([_layout_out(row) async for row in ReportLayout.objects.filter(project=project)])
    return out


@router.post("/{pid}/report-layouts", response=ReportLayoutOut)
async def create_report_layout(request, pid: uuid.UUID, data: ReportLayoutIn):
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    if not data.name.strip():
        raise HttpError(422, "a layout needs a name")
    problems = report_blocks.validate_blocks(data.blocks)
    if problems:
        raise HttpError(422, _problems_message(problems))
    layout = await ReportLayout.objects.acreate(
        project=project,
        name=data.name.strip(),
        description=data.description,
        blocks=data.blocks,
        show_index=data.show_index,
        numbering=data.numbering,
        revision=1,
        created_by=caller.subject,
        updated_by=caller.subject,
    )
    await _audit(request, "create", "report_layout", layout, pid)
    return _layout_out(layout)


@router.post("/{pid}/report-layouts/import", response=ReportLayoutOut)
async def import_report_layout(request, pid: uuid.UUID, data: LayoutImportIn):
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    problems = report_blocks.validate_blocks(data.blocks)
    if problems:
        raise HttpError(422, _problems_message(problems))
    layout = await ReportLayout.objects.acreate(
        project=project,
        name=(data.name.strip() or "Imported layout"),
        description=data.description,
        blocks=_fresh_instance_ids(data.blocks),
        show_index=data.show_index,
        numbering=data.numbering,
        revision=1,
        created_by=caller.subject,
        updated_by=caller.subject,
    )
    await _audit(request, "import", "report_layout", layout, pid)
    return _layout_out(layout)


@router.get("/{pid}/report-layouts/{layout_id}", response=ReportLayoutOut)
async def get_report_layout(request, pid: uuid.UUID, layout_id: str):
    project = await _project_for(request, pid)
    layout = await _resolve_layout(project, layout_id)
    if layout is None:
        raise HttpError(404, "no such layout")
    return _builtin_out(layout) if layout.get("built_in") else _layout_out(layout["row"])


@router.put("/{pid}/report-layouts/{layout_id}", response=ReportLayoutOut)
async def update_report_layout(request, pid: uuid.UUID, layout_id: str, data: ReportLayoutIn):
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    layout = await _resolve_layout(project, layout_id)
    if layout is None:
        raise HttpError(404, "no such layout")
    if layout.get("built_in"):
        raise HttpError(409, "a built-in layout is read-only; duplicate it to change it")
    problems = report_blocks.validate_blocks(data.blocks)
    if problems:
        raise HttpError(422, _problems_message(problems))
    row = layout["row"]
    row.name = data.name.strip() or row.name
    row.description = data.description
    row.blocks = data.blocks
    row.show_index = data.show_index
    row.numbering = data.numbering
    row.revision = row.revision + 1
    row.updated_by = caller.subject
    await row.asave()
    await _audit(request, "update", "report_layout", row, pid)
    return _layout_out(row)


@router.delete("/{pid}/report-layouts/{layout_id}", response={204: None})
async def delete_report_layout(request, pid: uuid.UUID, layout_id: str):
    project = await _project_for(request, pid, needed="editor")
    layout = await _resolve_layout(project, layout_id)
    if layout is None:
        raise HttpError(404, "no such layout")
    if layout.get("built_in"):
        raise HttpError(409, "a built-in layout cannot be deleted")
    row = layout["row"]
    await _audit(request, "delete", "report_layout", row, pid)
    await row.adelete()
    return 204, None


@router.post("/{pid}/report-layouts/{layout_id}/duplicate", response=ReportLayoutOut)
async def duplicate_report_layout(
    request, pid: uuid.UUID, layout_id: str, data: LayoutDuplicateIn | None = None
):
    """Copy a built-in or the project's own layout into a new project layout."""
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    source = await _resolve_layout(project, layout_id)
    if source is None:
        raise HttpError(404, "no such layout")
    name = (data.name if data else "") or f"{source['name']} (copy)"
    copy = await ReportLayout.objects.acreate(
        project=project,
        name=name.strip(),
        description=source.get("description", ""),
        blocks=_fresh_instance_ids(source.get("blocks", [])),
        show_index=source.get("show_index", True),
        numbering=source.get("numbering", True),
        revision=1,
        created_by=caller.subject,
        updated_by=caller.subject,
    )
    await _audit(request, "duplicate", "report_layout", copy, pid)
    return _layout_out(copy)


@router.get("/{pid}/report-layouts/{layout_id}/export", response=dict)
async def export_report_layout(request, pid: uuid.UUID, layout_id: str):
    """A layout as a portable file (import it into another project)."""
    project = await _project_for(request, pid)
    layout = await _resolve_layout(project, layout_id)
    if layout is None:
        raise HttpError(404, "no such layout")
    return {
        "format": "aisc-report-layout",
        "version": 1,
        "name": layout["name"],
        "description": layout.get("description", ""),
        "show_index": layout.get("show_index", True),
        "numbering": layout.get("numbering", True),
        "blocks": layout.get("blocks", []),
    }


@router.post("/{pid}/report-layouts/{layout_id}/preview", response=dict)
async def preview_report_layout(
    request, pid: uuid.UUID, layout_id: str, data: LayoutPreviewIn | None = None
):
    """Render a layout to HTML against a version, without saving a report."""
    project = await _project_for(request, pid)
    data = data or LayoutPreviewIn()
    if data.blocks is not None:
        layout = {
            "id": layout_id,
            "name": "Preview",
            "blocks": data.blocks,
            "show_index": True,
            "numbering": True,
            "revision": 0,
        }
    else:
        layout = await _resolve_layout(project, layout_id)
        if layout is None:
            raise HttpError(404, "no such layout")
    problems = report_blocks.validate_blocks(layout["blocks"])
    if problems:
        raise HttpError(422, _problems_message(problems))
    snapshot = await report_snapshot.build_snapshot(
        project, layout=layout, version_pid=data.version_pid or None, period=data.period,
    )
    document, _ = await report_render.render_document(snapshot, "html")
    return {
        "html": document.decode("utf-8"),
        "outline": report_blocks.outline(layout["blocks"]),
        "layout": {k: layout.get(k) for k in ("id", "name", "show_index", "numbering")},
    }


def _fresh_instance_ids(blocks: list[dict]) -> list[dict]:
    """A copy of blocks with new instance ids (so a duplicate is its own layout)."""
    out = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        out.append({**b, "instance_id": str(uuid.uuid4())})
    return out


def _problems_message(problems: list[dict]) -> str:
    return "; ".join(f"{p.get('pointer') or ''} {p.get('message')}".strip() for p in problems)


async def _audit(request, action: str, resource_type: str, row, pid) -> None:
    await sync_to_async(log_action)(
        request,
        action=action,
        resource_type=resource_type,
        resource_id=str(row.pid),
        metadata={"projectPid": str(pid)},
    )


# --- reports -------------------------------------------------------------------


async def _render_report(report_pid: str, snapshot: dict, fmt: str) -> None:
    """Render + store the document on a 'pending' report row, flipping it to
    'generated' (with the document) or 'failed'. Async so it can run in a
    worker thread (``_launch_worker``) or be awaited directly in tests."""
    try:
        document, content_type = await report_service.generate(snapshot, fmt)
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
    report.document_checksum = _checksum_bytes(document)
    update_fields = ["document", "format", "status", "file_ref", "document_checksum"]
    # a Word report also gets a PDF companion (the pilot's "a Word report also
    # gets a PDF copy"); the /pdf endpoint serves it without re-rendering.
    if actual_fmt == "docx":
        try:
            report.document_pdf, _ = await report_render.render_document(snapshot, "pdf")
            update_fields.append("document_pdf")
        except Exception as exc:                 # noqa: BLE001 - the Word doc still stands
            logger.warning("report %s PDF companion failed: %s", report_pid, exc)
    await report.asave(update_fields=update_fields)


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
    """Assemble the snapshot (scoped to the picked version, evaluations, sections
    and layout, falling back to the project's report settings), render a real
    PDF/Word document, store the report (document + frozen snapshot + checksums)
    and audit."""
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    settings = await ProjectReportSettings.objects.filter(project=project).afirst()
    configured = (
        settings.default_format
        if settings and settings.default_format in SETTINGS_FORMATS
        else "pdf"
    )
    fmt = data.format if data.format in FORMATS else configured
    sections = [s for s in data.sections if s in REPORT_SECTIONS] or (
        [s for s in (settings.default_sections or []) if s in REPORT_SECTIONS]
        if settings else []
    ) or list(REPORT_SECTIONS)

    layout = None
    if data.layout_id:
        layout = await _resolve_layout(project, data.layout_id)
        if layout is None:
            raise HttpError(404, "no such layout")
        problems = report_blocks.validate_blocks(layout.get("blocks") or [])
        if problems:
            raise HttpError(422, _problems_message(problems))

    evaluation_pids = []
    selected_db_ids: dict[int, str] = {}
    for epid in data.evaluation_pids or []:
        try:
            eqid = uuid.UUID(str(epid))
        except ValueError:
            raise HttpError(404, f"no such evaluation {epid!r}")
        evaluation = await Evaluation.objects.filter(project=project, pid=eqid).afirst()
        if evaluation is None:
            raise HttpError(404, f"no such evaluation {epid!r}")
        evaluation_pids.append(str(eqid))
        selected_db_ids[evaluation.id] = str(eqid)

    # a run on a local model is not part of the assessment record (pilot parity):
    # an evaluation whose runs are all local is refused when asked for by pid.
    only_local = await report_local_runs.only_local_evaluation_ids(list(selected_db_ids))
    if only_local:
        bad = ", ".join(sorted(selected_db_ids[i] for i in only_local))
        raise HttpError(422, f"a local-model run is not part of the assessment record: {bad}")

    try:
        snapshot = await report_snapshot.build_snapshot(
            project,
            evaluation_pids=evaluation_pids,
            sections=sections,
            version_pid=data.version_pid or None,
            layout=layout,
            period=data.period,
            other_versions=data.other_versions,
            compare_to=data.compare_to or None,
        )
    except report_snapshot.InvalidComparison:
        raise HttpError(422, "compare_to must name an earlier version of this project")

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

    version_ref = snapshot.get("system", {}).get("version") or {}
    version = None
    if version_ref.get("pid"):
        version = await SystemVersion.objects.filter(
            project=project, pid=version_ref["pid"]
        ).afirst()

    # The endpoint answers immediately: the row is created 'pending' and the
    # render runs in the background (_launch_worker), flipping it to
    # 'generated' (with the document) or 'failed'. The webapp polls the list.
    report = await Report.objects.acreate(
        project=project,
        system_version=version,
        layout=layout.get("row") if layout and not layout.get("built_in") else None,
        layout_revision=(layout.get("revision") if layout else None),
        period=data.period,
        version=next_version,
        format=fmt,
        snapshot=snapshot,
        snapshot_checksum=_checksum_json(snapshot),
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
            "layout": (layout or {}).get("id"),
            "version": next_version,
            "snapshotChecksum": report.snapshot_checksum,
            "systemVersion": version_ref.get("number"),
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
    fmt = data.default_format if data.default_format in SETTINGS_FORMATS else "pdf"
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


@router.get("/{pid}/reports/{report_pid}/snapshot", response=dict)
async def report_snapshot_view(request, pid: uuid.UUID, report_pid: uuid.UUID):
    """The frozen generation snapshot of a report and its checksum (provenance)."""
    project = await _project_for(request, pid)
    report = await Report.objects.filter(project=project, pid=report_pid).afirst()
    if report is None:
        raise HttpError(404, "no such report")
    return {
        "snapshot": report.snapshot or {},
        "snapshot_checksum": report.snapshot_checksum or "",
        "document_checksum": report.document_checksum or "",
        "format": report.format,
    }


def _document_bytes(report: Report) -> bytes:
    if report.document is None:
        raise HttpError(409, "this report has no rendered document")
    return bytes(report.document)


@router.get("/{pid}/reports/{report_pid}/pdf")
async def report_pdf(request, pid: uuid.UUID, report_pid: uuid.UUID):
    """Stream the report as a PDF: the document itself for a PDF report, the PDF
    companion of a Word report, or a fresh render of the frozen snapshot."""
    project = await _project_for(request, pid)
    report = await Report.objects.filter(project=project, pid=report_pid).afirst()
    if report is None:
        raise HttpError(404, "no such report")
    if report.document_pdf is not None:
        content = bytes(report.document_pdf)
    elif report.format == "pdf":
        content = _document_bytes(report)
    else:
        content, _ = await report_render.render_document(report.snapshot or {}, "pdf")
    filename = f"report-{report.file_ref.split('/')[-1] or report.pid}.pdf"
    return HttpResponse(
        content=content,
        content_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/{pid}/reports/{report_pid}/download")
async def download_report(request, pid: uuid.UUID, report_pid: uuid.UUID):
    """Stream the report's primary document (PDF or Word) with its own MIME type."""
    project = await _project_for(request, pid)
    report = await Report.objects.filter(project=project, pid=report_pid).afirst()
    if report is None:
        raise HttpError(404, "no such report")
    content = _document_bytes(report)
    content_type = report_render.CONTENT_TYPES.get(report.format, "application/octet-stream")
    filename = f"report-{report.pid}.{report.format}"
    return HttpResponse(
        content=content,
        content_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
