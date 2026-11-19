"""
The versioned evidence map, under /projects/{pid}/evidence/...

`GET /{pid}/evidence` returns the whole map for one AI-card version (the latest
when none is asked for): the objectives, the test and checklist tiles, the saved
links, coverage and the review. `PUT /{pid}/evidence/links` replaces the latest
version's links (an older one is kept as it was), and
`POST /{pid}/evidence/review` marks the latest version's map reviewed.

The map is assembled by `services.evidence_map`; the router only checks roles,
resolves the version and validates every link's two ends (both project-scoped).
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import caller_of, role_or_404
from aisc_controls.models import ProjectChecklist
from aisc_control_objectives.models import ControlObjective
from aisc_control_objectives.models import EvidenceLink, EvidenceReview
from aisc_backend.models.plugin import Plugin
from aisc_backend.models.project import Project
from aisc_backend.models.system import System, SystemVersion
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.evidence import EvidenceLinksIn, EvidenceMapOut, EvidenceReviewIn
from aisc_backend.services import evidence_map as map_service

router = Router(tags=["evidence"])

project_repository = ProjectRepository()


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


async def _latest_version(project: Project) -> SystemVersion | None:
    system = await System.objects.filter(project=project).afirst()
    if system is None:
        return None
    return await SystemVersion.objects.filter(system=system).order_by("-number").afirst()


async def _version_or_404(project: Project, version_pid: uuid.UUID) -> SystemVersion:
    system = await System.objects.filter(project=project).afirst()
    version = None
    if system is not None:
        version = await SystemVersion.objects.filter(
            system=system, pid=version_pid
        ).afirst()
    if version is None:
        raise HttpError(404, "no such AI card version in this project")
    return version


async def _build(request, project: Project, version_pid: str | None) -> dict:
    can_edit = False
    try:
        role = await role_or_404(request, project, "viewer")
        can_edit = role in ("editor", "owner")
    except HttpError:
        raise
    return await map_service.build_map(
        project, version_pid, can_edit=can_edit, caller_subject=caller_of(request).subject
    )


@router.get("/{pid}/evidence", response=EvidenceMapOut)
async def get_evidence_map(request, pid: uuid.UUID, version: str | None = None):
    project = await _project_for(request, pid)
    try:
        return await _build(request, project, version)
    except LookupError as exc:
        raise HttpError(404, str(exc))


async def _validate_links(project: Project, links: list) -> None:
    """Every link's objective and item must exist in THIS project (no cross-project IDs)."""
    packages = {
        plugin.package_name async for plugin in Plugin.objects.filter(project=project)
    }
    checklist_pids = {
        str(c.pid) async for c in ProjectChecklist.objects.filter(project=project)
    }
    visible_objectives = {
        o.objective_id
        async for o in ControlObjective.objects.filter(project__isnull=True)
    } | {o.objective_id async for o in ControlObjective.objects.filter(project=project)}
    seen: set[tuple[str, str, str]] = set()
    for link in links:
        if link.kind not in map_service.KINDS:
            raise HttpError(400, f"unknown link kind {link.kind!r}")
        if link.objective_id not in visible_objectives:
            raise HttpError(400, f"no such objective {link.objective_id}")
        if link.kind == "test" and link.key not in packages:
            raise HttpError(400, f"no installed test {link.key}")
        if link.kind == "control" and link.key not in checklist_pids:
            raise HttpError(400, f"no installed checklist {link.key}")
        if link.note and len(link.note) > 1000:
            raise HttpError(400, "a note is at most 1000 characters")
        token = (link.objective_id, link.kind, link.key)
        if token in seen:
            raise HttpError(400, f"duplicate link {link.kind}:{link.key}")
        seen.add(token)


@router.put("/{pid}/evidence/links", response=EvidenceMapOut)
async def put_evidence_links(request, pid: uuid.UUID, data: EvidenceLinksIn):
    project = await _project_for(request, pid, needed="editor")
    version = await _version_or_404(project, data.version)
    latest = await _latest_version(project)
    if latest is None or version.pk != latest.pk:
        raise HttpError(403, "an older version's map is kept as it was")
    await _validate_links(project, data.links)

    subject = caller_of(request).subject
    objectives = {
        o.objective_id: o
        async for o in ControlObjective.objects.filter(project__isnull=True)
    }
    objectives.update(
        {o.objective_id: o async for o in ControlObjective.objects.filter(project=project)}
    )

    await EvidenceLink.objects.filter(project=project, version=version).adelete()
    for link in data.links:
        await EvidenceLink.objects.acreate(
            project=project,
            version=version,
            objective=objectives[link.objective_id],
            kind=link.kind,
            key=link.key,
            note=(link.note or "").strip(),
            created_by=subject,
        )
    # The map is no longer the one that was reviewed.
    await EvidenceReview.objects.filter(version=version).adelete()
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="evidence_links",
        resource_id=str(version.pid),
        metadata={"projectPid": str(pid), "links": len(data.links)},
    )
    return await map_service.build_map(
        project, str(version.pid), can_edit=True, caller_subject=subject
    )


@router.post("/{pid}/evidence/review", response=EvidenceMapOut)
async def review_evidence_map(request, pid: uuid.UUID, data: EvidenceReviewIn):
    project = await _project_for(request, pid, needed="editor")
    version = await _version_or_404(project, data.version)
    latest = await _latest_version(project)
    if latest is None or version.pk != latest.pk:
        raise HttpError(403, "an older version's map is kept as it was")

    caller = caller_of(request)
    authors = {
        l.created_by
        async for l in EvidenceLink.objects.filter(project=project, version=version)
    }
    if caller.subject in authors:
        raise HttpError(403, "someone who made none of its links reviews the map")

    await EvidenceReview.objects.filter(version=version).adelete()
    await EvidenceReview.objects.acreate(
        project=project,
        version=version,
        reviewed_by=caller.subject,
        reviewed_by_name=caller.username or caller.email or caller.subject,
    )
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="evidence_review",
        resource_id=str(version.pid),
        metadata={"projectPid": str(pid)},
    )
    return await map_service.build_map(
        project, str(version.pid), can_edit=True, caller_subject=caller.subject
    )
