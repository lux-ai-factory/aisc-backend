"""
Qualification + AI-card-versioning endpoints.

Serves, from the backend's own single DB, under /projects/{pid}/...:
  - system-versions (list / latest / create) — the card-version contract the
    future webapp reads; the headless service's CardVersion shape is kept so
    nothing downstream changes.
  - questionnaire (get/select the project's active questionnaire) — the
    per-project choice of WHICH form a qualification is answered against.
  - qualification (orchestration and reads) — the backend owns the data; the
    compute service ("answers in, card out") is called and its result
    snapshotted + versioned + audited here.

All project-scoped: a member reads, an editor/owner writes (admin bypasses).
"""
import json
import uuid

from asgiref.sync import sync_to_async
from django.db import models
from ninja import File, Form, Router
from ninja.files import UploadedFile
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import caller_of, role_or_404
from aisc_backend.auth.keycloak import KeycloakAuth
from aisc_backend.flags import require_flag
from aisc_backend.models.system import SystemVersion
from aisc_qualification.models import Qualification, Questionnaire
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_qualification.routers.questionnaire import _latest_number
from aisc_qualification.schemas.qualification import (
    CardAgentMessageIn,
    CardComponentLinkOut,
    CardAgentTurnOut,
    PrefillOut,
    QualificationCardIn,
    QualificationDetailOut,
    QualificationIn,
    QuestionnaireSelectionOut,
    NodePatchIn,
    NodesPatchIn,
    SystemVersionIn,
    SystemVersionOut,
)
from aisc_backend.services import ai_assistant
from aisc_qualification.services import card_agent, card_components, publication, qualification_service
from aisc_qualification.services import qualification_prefill

router = Router(tags=["qualification"], auth=[require_flag("qualification"), KeycloakAuth()])

project_repository = ProjectRepository()
system_repository = SystemRepository()


def _version_out(row, project_pid: str) -> SystemVersionOut:
    return SystemVersionOut(
        pid=row.pid,
        project_id=project_pid,
        number=row.number,
        name=row.name,
        version=row.version,
        provider=row.provider,
        description=row.description,
        created_at=row.created_at.isoformat(),
        created_by=row.created_by,
    )


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


async def _resolve_questionnaire_version(
    project: Project, questionnaire_id: str, requested: int | None
) -> int | None:
    """The questionnaire version an answer set is filled against: the one the
    client asked for, else the questionnaire's latest version."""
    if requested is not None:
        return requested
    questionnaire = await _find_questionnaire(project, questionnaire_id)
    if questionnaire is None:
        return None
    latest = await _latest_number(questionnaire)
    return latest or None


async def _find_questionnaire(project: Project, questionnaire_id: str):
    """The questionnaire by id, but only one this project may use: a default
    (project NULL) or this project's own. A random id is None."""
    try:
        qid = uuid.UUID(questionnaire_id)
    except ValueError:
        return None
    return await (
        Questionnaire.objects.filter(pid=qid)
        .filter(models.Q(project__isnull=True) | models.Q(project=project))
        .afirst()
    )


# --- The system / questionnaire the project qualifies -------------------------


@router.get("/{pid}/system-versions", response=list[SystemVersionOut])
async def list_system_versions(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    system = await system_repository.get_or_create(project)
    versions = system_repository.iterate_versions(system)
    return [_version_out(row, str(project.pid)) async for row in versions]


@router.get("/{pid}/system-versions/latest", response=SystemVersionOut | None)
async def latest_system_version(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    system = await system_repository.get_or_create(project)
    row = await system_repository.latest(system)
    return _version_out(row, str(project.pid)) if row is not None else None


@router.get("/{pid}/system-versions/{number}", response=SystemVersionOut)
async def get_system_version(request, pid: uuid.UUID, number: int):
    """Read one published version by its number (its stable id).

    A published version never moves, so this still resolves an old version even
    after later drafts or versions exist."""
    project = await _project_for(request, pid)
    system = await system_repository.get_or_create(project)
    row = await SystemVersion.objects.filter(system=system, number=number).afirst()
    if row is None:
        raise HttpError(404, f"no version {number}")
    return _version_out(row, str(project.pid))


@router.post("/{pid}/system-versions", response=SystemVersionOut)
async def create_system_version(request, pid: uuid.UUID, data: SystemVersionIn):
    project = await _project_for(request, pid, needed="editor")
    system = await system_repository.get_or_create(project)
    caller = caller_of(request)
    row = await system_repository.create_version(
        system,
        name=data.name,
        version=data.version or "",
        provider=data.provider or "",
        description=data.description or "",
        created_by=caller.subject,
    )
    await sync_to_async(log_action)(
        request,
        action="create",
        resource_type="system_version",
        resource_id=str(row.pid),
        metadata={"projectPid": str(pid), "number": row.number, "name": row.name},
    )
    return _version_out(row, str(pid))


@router.get("/{pid}/questionnaire", response=QuestionnaireSelectionOut)
async def get_selection(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    system = await system_repository.get_or_create(project)
    return QuestionnaireSelectionOut(questionnaire_id=system.questionnaire_id or None)


@router.get("/{pid}/qualification/vocabularies", response=dict)
async def qualification_vocabularies(request, pid: uuid.UUID):
    """The VAIR term lists for the qualification form, per AIRO class.

    {} when the service is unconfigured or unreachable: the form then offers
    free text instead of term dropdowns.
    """
    await _project_for(request, pid)
    return await qualification_service.get_vocabularies()


@router.put("/{pid}/questionnaire", response=QuestionnaireSelectionOut)
async def select_questionnaire(request, pid: uuid.UUID, data: QuestionnaireSelectionOut):
    project = await _project_for(request, pid, needed="editor")
    system = await system_repository.get_or_create(project)
    system.questionnaire_id = data.questionnaire_id or ""
    await system.asave()
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="questionnaire_selection",
        resource_id=str(pid),
        metadata={"projectPid": str(pid), "questionnaire_id": system.questionnaire_id},
    )
    return QuestionnaireSelectionOut(questionnaire_id=system.questionnaire_id or None)


# --- Qualification orchestration -------------------------------------------------


async def _active_questionnaire_definition(project, questionnaire_id: str) -> dict:
    """The definition prefill reads questions from: the named one, else the project's."""
    qid = (questionnaire_id or "").strip()
    if not qid:
        system = await system_repository.get_or_create(project)
        qid = system.questionnaire_id or ""
    if not qid or qid == qualification_service.DEFAULT_QUESTIONNAIRE_ID:
        return await qualification_service.get_default_questionnaire()
    row = await _find_questionnaire(project, qid)
    if row is None:
        raise HttpError(404, "no such questionnaire")
    return row.definition or {}


@router.post("/{pid}/qualification/prefill", response=PrefillOut)
async def prefill_qualification(request, pid: uuid.UUID, file: File[UploadedFile],
                                questionnaire_id: str = "",
                                mode: str = Form("empty"),
                                current: str = Form("{}")):
    """Propose answers from an uploaded document (PDF, Word or plain text).

    The pilot's own rules, text only — no model (see
    `services.qualification_prefill`): the document's lines are read against the
    form's questions, its labelled metadata, its risks and its VAIR lists, and
    everything found is *proposed*. `current` is the form as it stands and
    `mode` the merge the person chose: "empty" fills blanks only, "replace"
    takes the document wherever it has something, and a document that says
    nothing about a field never erases it. Nothing is stored.
    """
    project = await _project_for(request, pid, needed="editor")
    # reading the upload is a sync file hit: never do it on the event loop
    aread = getattr(file, "aread", None)
    blob = await aread() if aread is not None else await sync_to_async(file.read)()
    filename = getattr(file, "name", "") or ""
    try:
        qualification_prefill.check_size(blob)
    except qualification_prefill.UploadTooLarge as exc:
        raise HttpError(413, str(exc))
    try:
        text = qualification_prefill.extract_text(filename, blob)
    except qualification_prefill.UnsupportedDocument as exc:
        raise HttpError(415, str(exc))
    except Exception as exc:  # noqa: BLE001 - a broken document is the caller's problem
        raise HttpError(415, f"{filename} could not be read: {exc}")
    try:
        before = json.loads(current or "{}")
        if not isinstance(before, dict):
            raise ValueError("the form's answers are not an object")
    except ValueError as exc:
        raise HttpError(422, f"current: {exc}")
    definition = await _active_questionnaire_definition(project, questionnaire_id)
    try:
        read = qualification_prefill.prefill(definition, text, before, mode)
    except qualification_prefill.UnknownMode as exc:
        raise HttpError(422, str(exc))
    questions = definition.get("questions") if isinstance(definition, dict) else None
    note = "" if isinstance(questions, list) else (
        "this questionnaire has no answerable questions to pre-fill"
    )
    await sync_to_async(log_action)(
        request,
        action="create",
        resource_type="qualification_prefill",
        resource_id=str(pid),
        metadata={
            "filename": filename,
            "mode": mode,
            "proposals": len(read["proposed"]) + len(read["profileFilled"]),
            "risks": read["risksProposed"],
        },
    )
    return PrefillOut(
        filename=filename,
        characters=len(text),
        note=note,
        source=read["source"],
        model=read["model"],
        values=read["values"],
        filled=read["filled"],
        kept=read["kept"],
        proposed=read["proposed"],
        profile=read["profile"],
        profileFilled=read["profileFilled"],
        tags=read["tags"],
        tagsFilled=read["tagsFilled"],
        risks=read["risks"],
        risksKept=read["risksKept"],
        risksProposed=read["risksProposed"],
        picks=read["picks"],
        picksProposed=read["picksProposed"],
        unmatched=read["unmatched"] if isinstance(questions, list) else [],
        unplaced=read["unplaced"],
    )


@router.get(
    "/{pid}/qualification/{qualification_pid}/card-components",
    response=list[CardComponentLinkOut],
)
async def qualification_card_components(
    request, pid: uuid.UUID, qualification_pid: uuid.UUID
):
    """The card's components and the engine components they stand for.

    Recorded on every save with a snapshot of each, so this reports what changed
    in the engine since (`changed`) and which card component has no engine
    counterpart (`missing`).
    """
    project = await _project_for(request, pid)
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    rows = await card_components.read(project, qualification)
    return [CardComponentLinkOut(**row) for row in rows]


# NOTE: declared before the `/{pid}/qualification/{qualification_pid}` routes on
# purpose — Ninja matches in registration order and would otherwise read `prefill`
# as a qualification id.
@router.get("/{pid}/qualification", response=list[QualificationDetailOut])
async def list_qualifications(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    return [
        await _qualification_detail(qualification, str(project.pid))
        async for qualification in Qualification.objects.filter(project=project)
        .order_by("-created_at")
    ]


@router.post("/{pid}/qualification", response=QualificationDetailOut)
async def save_qualification(request, pid: uuid.UUID, data: QualificationIn):
    """Save draft answers (B2): stored with status "draft", no card is built
    and no card version is bumped — only completing versions."""
    project = await _project_for(request, pid, needed="editor")

    qualification = await Qualification.objects.acreate(
        project=project,
        system_version=None,
        questionnaire_id=data.questionnaire_id,
        questionnaire_version=await _resolve_questionnaire_version(
            project, data.questionnaire_id, data.questionnaire_version
        ),
        answers=data.answers or {},
        system_card=None,
        status="draft",
    )
    await card_components.link(project, qualification)
    await sync_to_async(log_action)(
        request,
        action="create",
        resource_type="qualification",
        resource_id=str(qualification.pid),
        metadata={
            "projectPid": str(pid),
            "questionnaire_id": data.questionnaire_id,
            "name": data.name or project.name,
        },
    )
    return await _qualification_detail(qualification, str(project.pid))


@router.post("/{pid}/qualification/{qualification_pid}/complete", response=QualificationDetailOut)
async def complete_qualification(request, pid: uuid.UUID, qualification_pid: uuid.UUID):
    """Complete a draft (B2): publish it as the next assessed-system version.

    The explicit action that cuts a version (see `services.publication`). The
    snapshot it produces is immutable, so completing twice is refused: editing or
    reopening builds a successor instead."""
    return await _publish_qualification(request, pid, qualification_pid, action="complete")


@router.post("/{pid}/qualification/{qualification_pid}/publish", response=QualificationDetailOut)
async def publish_qualification(request, pid: uuid.UUID, qualification_pid: uuid.UUID):
    """Publish a draft as the next assessed-system version (the explicit action).

    Same ONE transactional operation as `complete`, named for what it does: it
    cuts the next `SystemVersion` and freezes the qualification as an immutable
    snapshot. Nothing cuts a version implicitly."""
    return await _publish_qualification(request, pid, qualification_pid, action="publish")


async def _publish_qualification(
    request, pid: uuid.UUID, qualification_pid: uuid.UUID, *, action: str
) -> QualificationDetailOut:
    """Build the card and publish the draft through the one publish operation."""
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    if qualification.status == "completed":
        raise HttpError(400, "already published")

    # The backend owns user questionnaires; the builtin default lives on the
    # headless service. An empty or default id resolves there; anything else
    # reads the backend row (unknown ids keep the established empty shape).
    questionnaire = {}
    qid = qualification.questionnaire_id or ""
    if not qid or qid == qualification_service.DEFAULT_QUESTIONNAIRE_ID:
        questionnaire = await qualification_service.get_default_questionnaire()
    else:
        q = await _find_questionnaire(project, qid)
        if q is not None:
            questionnaire = q.definition or {}

    card = await qualification_service.build_card(
        str(pid),
        qualification.questionnaire_id,
        questionnaire,
        qualification.answers or {},
        system={"name": project.name, "version": "", "company": ""},
    )
    questionnaire_version = qualification.questionnaire_version
    if questionnaire_version is None:
        questionnaire_version = await _resolve_questionnaire_version(
            project, qualification.questionnaire_id, None
        )
    qualification, version = await publication.publish(
        project,
        qualification,
        card=card,
        created_by=caller.subject,
        questionnaire_version=questionnaire_version,
    )
    await card_components.link(project, qualification)
    await sync_to_async(log_action)(
        request,
        action=action,
        resource_type="qualification",
        resource_id=str(qualification.pid),
        metadata={
            "projectPid": str(pid),
            "systemVersion": version.number,
            "questionnaire_id": qualification.questionnaire_id,
        },
    )
    return await _qualification_detail(qualification, str(project.pid))


@router.post("/{pid}/qualification/{qualification_pid}/reopen", response=QualificationDetailOut)
async def reopen_qualification(request, pid: uuid.UUID, qualification_pid: uuid.UUID):
    """Reopen a published qualification as a NEW draft successor.

    The published snapshot is never mutated: reopening copies its answers (and
    its card, until the next publish rebuilds it) into a fresh draft row. Only
    one draft may be open at a time, so a second reopen is refused."""
    project = await _project_for(request, pid, needed="editor")
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    if qualification.status != "completed":
        raise HttpError(400, "only a completed qualification can be reopened")
    open_draft = await Qualification.objects.filter(
        project=project, status="draft"
    ).afirst()
    if open_draft is not None:
        raise HttpError(400, "a draft is already open; finish or discard it first")
    draft = await Qualification.objects.acreate(
        project=project,
        system_version=None,
        questionnaire_id=qualification.questionnaire_id,
        questionnaire_version=qualification.questionnaire_version,
        answers=qualification.answers,
        system_card=qualification.system_card,
        status="draft",
    )
    await sync_to_async(log_action)(
        request,
        action="reopen",
        resource_type="qualification",
        resource_id=str(draft.pid),
        metadata={
            "projectPid": str(pid),
            "from": str(qualification.pid),
            "questionnaire_id": draft.questionnaire_id,
        },
    )
    return await _qualification_detail(draft, str(project.pid))


@router.get(
    "/{pid}/qualification/{qualification_pid}/versions",
    response=list[QualificationDetailOut],
)
async def list_qualification_versions(request, pid: uuid.UUID, qualification_pid: uuid.UUID):
    """The version chain of a qualification, oldest first (B2/B3): the draft
    plus every completed and card-edited row."""
    project = await _project_for(request, pid)
    anchor = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if anchor is None:
        raise HttpError(404, "no such qualification")
    rows = (
        Qualification.objects.filter(project=project)
        .select_related("system_version")
        .order_by("created_at")
    )
    out = []
    async for row in rows:
        version = (
            _version_out(row.system_version, str(project.pid))
            if row.system_version is not None
            else None
        )
        out.append(
            QualificationDetailOut(
                pid=row.pid,
                questionnaire_id=row.questionnaire_id,
                questionnaire_version=row.questionnaire_version,
                answers=row.answers,
                system_card=row.system_card,
                status=row.status,
                system_version=version,
                system_card_jsonld=qualification_service.card_jsonld(row.system_card),
            )
        )
    return out


@router.put("/{pid}/qualification/{qualification_pid}/card", response=QualificationDetailOut)
async def edit_qualification_card(
    request, pid: uuid.UUID, qualification_pid: uuid.UUID, data: QualificationCardIn
):
    """Store edited card values as a NEW qualification row on the next card
    version (B3). The completed row is never mutated."""
    project = await _project_for(request, pid, needed="editor")
    caller = caller_of(request)
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    if qualification.status != "completed" or qualification.system_version_id is None:
        raise HttpError(400, "only a completed qualification can be re-versioned")
    current = await SystemVersion.objects.filter(
        pk=qualification.system_version_id
    ).afirst()
    if current is None:
        raise HttpError(404, "no such card version")

    edited, version = await publication.publish(
        project,
        qualification,
        card=data.system_card,
        answers=qualification.answers,
        created_by=caller.subject,
        successor=True,
        name=current.name,
        version=current.version,
        provider=current.provider,
        description=current.description,
    )
    await card_components.link(project, edited)
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="qualification",
        resource_id=str(edited.pid),
        metadata={
            "projectPid": str(pid),
            "systemVersion": version.number,
            "from": str(qualification.pid),
        },
    )
    return await _qualification_detail(edited, str(project.pid))


@router.put(
    "/{pid}/qualification/{qualification_pid}/nodes/{node_id}",
    response=QualificationDetailOut,
)
async def patch_qualification_node(
    request,
    pid: uuid.UUID,
    qualification_pid: uuid.UUID,
    node_id: str,
    data: NodePatchIn,
):
    """Record one reviewer's correction to one card node and rebuild.

    The correction is merged into the answers' `__patch` (so it survives
    rebuilds) and the card is rebuilt by the ontology with it applied — view,
    graph and digest stay consistent. Stored as a new version, like a card
    edit; an empty change removes the node's correction. A refused correction
    (e.g. an unknown VAIR term) stores nothing.
    """
    project = await _project_for(request, pid, needed="editor")
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    if qualification.status != "completed":
        raise HttpError(400, "only a completed qualification can be corrected")
    change = data.model_dump(exclude_unset=True)
    if "label" in change and not change["label"].strip():
        raise HttpError(400, "label must not be empty")
    answers = dict(qualification.answers or {})
    patch = answers.get("__patch")
    patch = dict(patch) if isinstance(patch, dict) else {}
    if not change:
        patch.pop(node_id, None)
    else:
        merged = dict(patch.get(node_id) or {})
        merged.update(change)
        patch[node_id] = merged
    if patch:
        answers["__patch"] = patch
    else:
        answers.pop("__patch", None)

    return await _store_corrected_version(
        request, project, qualification, answers, {"node": node_id}
    )


async def _store_corrected_version(
    request,
    project,
    qualification: Qualification,
    answers: dict,
    extra_metadata: dict,
) -> QualificationDetailOut:
    """Rebuild the card with corrected answers and store it as ONE new version."""
    questionnaire = {}
    qid = qualification.questionnaire_id or ""
    if not qid or qid == qualification_service.DEFAULT_QUESTIONNAIRE_ID:
        questionnaire = await qualification_service.get_default_questionnaire()
    else:
        q = await _find_questionnaire(project, qid)
        if q is not None:
            questionnaire = q.definition or {}

    try:
        card = await qualification_service.build_card(
            str(project.pid),
            qualification.questionnaire_id,
            questionnaire,
            answers,
            system={"name": project.name, "version": "", "company": ""},
        )
    except qualification_service.QualificationServiceError as exc:
        raise HttpError(502, str(exc))
    if "system" not in (card.get("view") or {}):
        raise HttpError(422, "the ontology refused the correction")

    caller = caller_of(request)
    current = await SystemVersion.objects.filter(
        pk=qualification.system_version_id
    ).afirst()
    if current is None:
        raise HttpError(404, "no such card version")

    edited, version = await publication.publish(
        project,
        qualification,
        card=card,
        answers=answers,
        created_by=caller.subject,
        questionnaire_version=qualification.questionnaire_version,
        successor=True,
        name=current.name,
        version=current.version,
        provider=current.provider,
        description=current.description,
    )
    await card_components.link(project, edited)
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="qualification",
        resource_id=str(edited.pid),
        metadata={
            "projectPid": str(project.pid),
            "systemVersion": version.number,
            "from": str(qualification.pid),
            **extra_metadata,
        },
    )
    return await _qualification_detail(edited, str(project.pid))


@router.put(
    "/{pid}/qualification/{qualification_pid}/nodes",
    response=QualificationDetailOut,
)
async def patch_qualification_nodes(
    request,
    pid: uuid.UUID,
    qualification_pid: uuid.UUID,
    data: NodesPatchIn,
):
    """Save a card's staged node corrections as ONE new version.

    `nodes` maps node id to its correction (same keys as the single-node
    endpoint); an empty correction clears that node's correction. The whole
    set replaces the stored `__patch`, so the card is rebuilt once with every
    edit applied and stored as a single version."""
    project = await _project_for(request, pid, needed="editor")
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    if qualification.status != "completed":
        raise HttpError(400, "only a completed qualification can be corrected")
    patch: dict = {}
    for node_id, node in data.nodes.items():
        change = node.model_dump(exclude_unset=True)
        if not change:
            continue
        if "label" in change and not str(change["label"] or "").strip():
            raise HttpError(400, "label must not be empty")
        patch[node_id] = change
    answers = dict(qualification.answers or {})
    if patch:
        answers["__patch"] = patch
    else:
        answers.pop("__patch", None)
    return await _store_corrected_version(
        request, project, qualification, answers, {"nodes": sorted(patch)}
    )


@router.get("/{pid}/qualification/{qualification_pid}", response=QualificationDetailOut)
async def get_qualification(request, pid: uuid.UUID, qualification_pid: uuid.UUID):
    project = await _project_for(request, pid)
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    return await _qualification_detail(qualification, str(project.pid))


async def _qualification_detail(
    qualification: Qualification, project_pid: str
) -> QualificationDetailOut:
    version = None
    if qualification.system_version_id is not None:
        row = await SystemVersion.objects.filter(
            pk=qualification.system_version_id
        ).afirst()
        version = _version_out(row, project_pid) if row is not None else None
    return QualificationDetailOut(
        pid=qualification.pid,
        questionnaire_id=qualification.questionnaire_id,
        questionnaire_version=qualification.questionnaire_version,
        answers=qualification.answers,
        system_card=qualification.system_card,
        status=qualification.status,
        system_version=version,
        system_card_jsonld=qualification_service.card_jsonld(qualification.system_card),
    )


@router.post(
    "/{pid}/qualification/{qualification_pid}/agent",
    response=CardAgentTurnOut,
)
async def card_agent_turn(
    request, pid: uuid.UUID, qualification_pid: uuid.UUID, data: CardAgentMessageIn
):
    """Talk to the card agent about a completed qualification's card.

    The agent answers and **proposes** corrections — each one quoting the
    person's words or the card — but writes nothing: the client applies the
    accepted ones through `PUT .../nodes/{node_id}`, the single write path,
    which rebuilds the card and stores it as a new version. 503 when the project
    has no model configured.
    """
    project = await _project_for(request, pid, needed="editor")
    qualification = await Qualification.objects.filter(
        project=project, pid=qualification_pid
    ).afirst()
    if qualification is None:
        raise HttpError(404, "no such qualification")
    if qualification.status != "completed":
        raise HttpError(400, "only a completed qualification has a card to talk about")
    message = (data.message or "").strip()
    if not message:
        raise HttpError(400, "say something to the agent")

    try:
        ask = await card_agent.resolve_agent(project)
    except ai_assistant.NoModelConfigured as exc:
        raise HttpError(503, str(exc))
    except ai_assistant.DestinationRefused as exc:
        raise HttpError(400, str(exc))

    patch = (qualification.answers or {}).get("__patch")
    patch = patch if isinstance(patch, dict) else None
    reply = await ask(qualification.system_card or {}, message, patch)
    turn = card_agent.review(
        card=qualification.system_card or {}, message=message, reply=reply, patch=patch
    )
    await sync_to_async(log_action)(
        request,
        action="create",
        resource_type="qualification_agent",
        resource_id=str(qualification.pid),
        metadata={
            "projectPid": str(pid),
            "proposals": len(turn["proposals"]),
            "dropped": len(turn["dropped"]),
        },
    )
    return CardAgentTurnOut(**turn)
