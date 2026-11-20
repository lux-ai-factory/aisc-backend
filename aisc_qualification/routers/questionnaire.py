"""
Questionnaire management: the forms a qualification is answered against.

Handled entirely in the backend (the single DB): add, list, get, update,
remove a questionnaire (a JSON form-schema document).

Two kinds:
- defaults: `project` is NULL, the service's built-in questionnaires, visible
  to every project;
- project ones: created inside a project and visible ONLY there.

Per-project selection of which questionnaire is used lives on the project
(System.questionnaire_id) and is served by the qualification router.
"""
import uuid

from asgiref.sync import sync_to_async
from django.db.models import Q
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_qualification.models import Questionnaire, QuestionnaireVersion
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_qualification.schemas.qualification import (
    DefaultQuestionnaireOut,
    QuestionnaireIn,
    QuestionnaireOut,
    QuestionnaireVersionOut,
)

router = Router(tags=["questionnaire"])

project_repository = ProjectRepository()


async def _out(row: Questionnaire) -> QuestionnaireOut:
    version = 0
    if row.pk is not None:
        last = await (
            QuestionnaireVersion.objects.filter(questionnaire=row)
            .order_by("-number")
            .afirst()
        )
        version = last.number if last is not None else 0
    return QuestionnaireOut(
        pid=row.pid, name=row.name,
        definition=row.definition, version=version,
    )


def _version_out(row: QuestionnaireVersion) -> QuestionnaireVersionOut:
    # `questionnaire` must be select_related by the caller: traversing the
    # relation then is cached, never a sync DB hit in async code.
    return QuestionnaireVersionOut(
        pid=row.pid,
        questionnaire_id=row.questionnaire.pid,
        number=row.number,
        definition=row.definition,
        created_at=row.created_at.isoformat(),
        created_by=row.created_by,
    )


async def _create_version(
    questionnaire: Questionnaire, definition: dict, created_by: str
) -> QuestionnaireVersion:
    """Append the next revision of a questionnaire (B1, append-only)."""
    last = await (
        QuestionnaireVersion.objects.filter(questionnaire=questionnaire)
        .order_by("-number")
        .afirst()
    )
    return await QuestionnaireVersion.objects.acreate(
        questionnaire=questionnaire,
        number=(last.number + 1) if last is not None else 1,
        name=questionnaire.name,
        description="",
        definition=definition or {},
        created_by=created_by,
    )


async def _apply_update(
    row: Questionnaire, data: QuestionnaireIn, created_by: str
) -> QuestionnaireVersion | None:
    """Save name; append a version only when the questions/fields change.

    Answers never reach this path (they belong to qualifications), so a version
    is defined by the definition alone."""
    definition_changed = bool(data.definition) and data.definition != row.definition
    row.name = data.name
    if definition_changed:
        row.definition = data.definition
    await row.asave()
    if definition_changed:
        return await _create_version(row, row.definition, created_by)
    return None


async def _latest_number(questionnaire: Questionnaire) -> int:
    last = await (
        QuestionnaireVersion.objects.filter(questionnaire=questionnaire)
        .order_by("-number")
        .afirst()
    )
    return last.number if last is not None else 0


async def _visible_questionnaire(project_pid: uuid.UUID | None, questionnaire_pid: uuid.UUID):
    """The questionnaire by id, limited to what the scope may see: defaults
    (project NULL) everywhere, project-owned ones only in their project."""
    qs = Questionnaire.objects.filter(pid=questionnaire_pid)
    if project_pid is not None:
        project = await project_repository.get(project_pid)
        qs = qs.filter(Q(project__isnull=True) | Q(project=project))
    return await qs.afirst()


async def _version_list(questionnaire: Questionnaire) -> list[QuestionnaireVersionOut]:
    return [
        _version_out(row)
        async for row in QuestionnaireVersion.objects.filter(questionnaire=questionnaire)
        .select_related("questionnaire")
        .order_by("number")
    ]


def _all_for(project_pid: uuid.UUID | None):
    qs = Questionnaire.objects.all()
    if project_pid is None:
        return qs.filter(project__isnull=True)
    return qs.filter(Q(project__isnull=True) | Q(project__pid=project_pid))


@router.get("", response=list[QuestionnaireOut])
async def list_questionnaires(request):
    return [await _out(row) async for row in _all_for(None)]


@router.get("/default", response=DefaultQuestionnaireOut)
async def get_default_questionnaire(request):
    from aisc_qualification.services import qualification_service
    from aisc_qualification.services.qualification_service import QualificationServiceError

    try:
        doc = await qualification_service.get_default_questionnaire()
    except QualificationServiceError as exc:
        raise HttpError(502, str(exc)) from exc
    if not doc:
        raise HttpError(404, "no default questionnaire configured")
    return DefaultQuestionnaireOut(
        id=doc.get("questionnaireId") or qualification_service.DEFAULT_QUESTIONNAIRE_ID,
        name=doc.get("questionnaireName") or "Annex IV default",
        description=doc.get("description") or "",
        version=doc.get("versionNumber") or 1,
        definition=doc,
    )


@router.post("", response=QuestionnaireOut)
async def add_questionnaire(request, data: QuestionnaireIn):
    from aisc_backend.auth.access import caller_of
    row = await Questionnaire.objects.acreate(
        name=data.name, definition=data.definition or {},
        project=None,
    )
    await _create_version(row, row.definition, caller_of(request).subject)
    await sync_to_async(log_action)(
        request, action="create", resource_type="questionnaire",
        resource_id=str(row.pid), metadata={"name": row.name})
    return await _out(row)


@router.put("/{questionnaire_pid}", response=QuestionnaireOut)
async def update_questionnaire(request, questionnaire_pid: uuid.UUID, data: QuestionnaireIn):
    from aisc_backend.auth.access import caller_of
    row = await Questionnaire.objects.aget(pid=questionnaire_pid)
    version = await _apply_update(row, data, caller_of(request).subject)
    await sync_to_async(log_action)(
        request, action="update", resource_type="questionnaire",
        resource_id=str(questionnaire_pid),
        metadata={"name": data.name, "version": version.number if version else None})
    return await _out(row)


@router.delete("/{questionnaire_pid}", response={204: None})
async def remove_questionnaire(request, questionnaire_pid: uuid.UUID):
    row = await Questionnaire.objects.aget(pid=questionnaire_pid)
    if row.project_id is not None:
        raise HttpError(400, "a project questionnaire is removed via the project")
    await row.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="questionnaire",
        resource_id=str(questionnaire_pid), metadata={})
    return 204, None


@router.get("/projects/{pid}", response=list[QuestionnaireOut])
async def list_project_questionnaires(request, pid: uuid.UUID):
    project = await project_repository.get(pid)
    from aisc_backend.auth.access import role_or_404
    await role_or_404(request, project)
    return [await _out(row) async for row in _all_for(pid)]


@router.post("/projects/{pid}", response=QuestionnaireOut)
async def add_project_questionnaire(request, pid: uuid.UUID, data: QuestionnaireIn):
    from aisc_backend.auth.access import caller_of, role_or_404
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed="editor")
    row = await Questionnaire.objects.acreate(
        name=data.name, definition=data.definition or {},
        project=project,
    )
    await _create_version(row, row.definition, caller_of(request).subject)
    await sync_to_async(log_action)(
        request, action="create", resource_type="questionnaire",
        resource_id=str(row.pid), metadata={"name": row.name, "projectPid": str(pid)})
    return await _out(row)


@router.put("/projects/{pid}/{questionnaire_pid}", response=QuestionnaireOut)
async def update_project_questionnaire(
    request, pid: uuid.UUID, questionnaire_pid: uuid.UUID, data: QuestionnaireIn
):
    from aisc_backend.auth.access import caller_of, role_or_404
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed="editor")
    row = await Questionnaire.objects.filter(pid=questionnaire_pid, project=project).afirst()
    if row is None:
        raise HttpError(404, "no such project questionnaire")
    version = await _apply_update(row, data, caller_of(request).subject)
    await sync_to_async(log_action)(
        request, action="update", resource_type="questionnaire",
        resource_id=str(questionnaire_pid),
        metadata={"name": data.name, "projectPid": str(pid),
                  "version": version.number if version else None})
    return await _out(row)


@router.delete("/projects/{pid}/{questionnaire_pid}", response={204: None})
async def remove_project_questionnaire(request, pid: uuid.UUID, questionnaire_pid: uuid.UUID):
    from aisc_backend.auth.access import role_or_404
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed="editor")
    row = await Questionnaire.objects.filter(pid=questionnaire_pid, project=project).afirst()
    if row is None:
        raise HttpError(404, "no such project questionnaire")
    await row.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="questionnaire",
        resource_id=str(questionnaire_pid), metadata={"projectPid": str(pid)})
    return 204, None


@router.get("/{questionnaire_pid}/versions", response=list[QuestionnaireVersionOut])
async def list_questionnaire_versions(request, questionnaire_pid: uuid.UUID):
    row = await _visible_questionnaire(None, questionnaire_pid)
    if row is None:
        raise HttpError(404, "no such questionnaire")
    return await _version_list(row)


@router.get("/{questionnaire_pid}/versions/{number}", response=QuestionnaireVersionOut)
async def get_questionnaire_version(
    request, questionnaire_pid: uuid.UUID, number: int
):
    row = await _visible_questionnaire(None, questionnaire_pid)
    if row is None:
        raise HttpError(404, "no such questionnaire")
    version = await (
        QuestionnaireVersion.objects.filter(questionnaire=row, number=number)
        .select_related("questionnaire")
        .afirst()
    )
    if version is None:
        raise HttpError(404, "no such questionnaire version")
    return _version_out(version)


@router.get("/projects/{pid}/{questionnaire_pid}/versions", response=list[QuestionnaireVersionOut])
async def list_project_questionnaire_versions(
    request, pid: uuid.UUID, questionnaire_pid: uuid.UUID
):
    from aisc_backend.auth.access import role_or_404
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    row = await _visible_questionnaire(pid, questionnaire_pid)
    if row is None:
        raise HttpError(404, "no such project questionnaire")
    return await _version_list(row)


@router.get(
    "/projects/{pid}/{questionnaire_pid}/versions/{number}",
    response=QuestionnaireVersionOut,
)
async def get_project_questionnaire_version(
    request, pid: uuid.UUID, questionnaire_pid: uuid.UUID, number: int
):
    from aisc_backend.auth.access import role_or_404
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    row = await _visible_questionnaire(pid, questionnaire_pid)
    if row is None:
        raise HttpError(404, "no such project questionnaire")
    version = await (
        QuestionnaireVersion.objects.filter(questionnaire=row, number=number)
        .select_related("questionnaire")
        .afirst()
    )
    if version is None:
        raise HttpError(404, "no such questionnaire version")
    return _version_out(version)
