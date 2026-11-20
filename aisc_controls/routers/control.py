"""
Controls / compliance checklists for a project, under /projects/{pid}/controls/*.

The checklist library is the catalogue templates installed into the project
(`ProjectChecklist`); each is answered through `Submission` with the reference
lifecycle (Draft -> Closed -> reopen as a new version, archive). Coverage and
readiness are computed on read by services.controls_scoring — never stored —
and the answers' scores are the 1-5 scale documented in Scoring.
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import role_or_404
from aisc_backend.auth.keycloak import KeycloakAuth
from aisc_backend.flags import require_flag
from aisc_controls.models import ControlQuestion, ControlSource, ControlTemplate, ProjectChecklist, Submission, SubmissionAnswer, SubmissionStatus
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_controls.schemas.control import (
    ControlQuestionOut,
    CustomChecklistIn,
    ProjectChecklistDetailOut,
    ProjectChecklistOut,
    SubmissionAnswerIn,
    SubmissionAnswerOut,
    SubmissionDetailOut,
    SubmissionOut,
)
from aisc_controls.services import controls_scoring

router = Router(tags=["controls"], auth=[require_flag("controls"), KeycloakAuth()])

project_repository = ProjectRepository()


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


def _checklist_out(cl: ProjectChecklist, question_count: int | None = None) -> ProjectChecklistOut:
    return ProjectChecklistOut(
        pid=cl.pid,
        template={
            "catalogue_id": cl.template.catalogue_id,
            "title": cl.name,
            "source": cl.template.source.name,
            "control_topic": cl.control_topic,
            "country_ids": cl.country_ids or [],
            "regulation_ids": cl.regulation_ids or [],
            "question_count": question_count or 0,
        },
        question_count=question_count or 0,
    )


async def _checklist_out_with_submissions(
    cl: ProjectChecklist, question_count: int | None = None
) -> ProjectChecklistOut:
    """The checklist row plus its latest submission summary (version, status,
    readiness) so cards can show where each checklist stands at a glance."""
    out = _checklist_out(cl, question_count)
    submissions = [
        sub
        async for sub in Submission.objects.filter(checklist=cl)
        .select_related("checklist__template")
        .order_by("-version")
    ]
    out.submission_count = len(submissions)
    if submissions:
        latest = submissions[0]
        out.latest_version = latest.version
        out.latest_status = latest.status
        answered = await SubmissionAnswer.objects.filter(
            submission=latest, score__isnull=False
        ).acount()
        total = await ControlQuestion.objects.filter(template=cl.template).acount()
        scores = [
            a.score
            async for a in SubmissionAnswer.objects.filter(submission=latest).only("score")
        ]
        if answered and total:
            out.latest_readiness = controls_scoring.readiness_percent(scores)
    return out


async def _submission_out(sub: Submission) -> SubmissionOut:
    total = await ControlQuestion.objects.filter(
        template_id=sub.checklist.template_id
    ).acount()
    answered = await SubmissionAnswer.objects.filter(submission=sub, score__isnull=False).acount()
    cov = controls_scoring.coverage(answered, total)
    scores = [a.score for a in
              [a async for a in SubmissionAnswer.objects.filter(submission=sub).only("score")]
              if a.score is not None]
    return SubmissionOut(
        pid=sub.pid,
        checklist_pid=sub.checklist.pid,
        label=sub.label,
        status=sub.status,
        version=sub.version,
        coverage=cov,
        readiness=controls_scoring.readiness_percent(scores),
    )


async def _submission_detail(sub: Submission) -> SubmissionDetailOut:
    """The submission with its answers, in question order."""
    base = (await _submission_out(sub)).model_dump()
    answers = [
        SubmissionAnswerOut(
            question_order=a.question.order, answer=a.answer, score=a.score,
        )
        for a in await _fetch_answers_with_questions(sub)
    ]
    base["answers"] = answers
    return SubmissionDetailOut(**base)


async def _fetch_answers_with_questions(sub: Submission):
    """The submission's answers joined with their question order, ordered."""
    rows = []
    async for a in SubmissionAnswer.objects.filter(submission=sub) \
            .select_related("question"):
        rows.append(a)
    rows.sort(key=lambda a: a.question.order)
    return rows


# --- the project's checklist library ---------------------------------------------


@router.get("/{pid}/controls", response=list[ProjectChecklistOut])
async def list_library(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    out = []
    async for cl in (
        ProjectChecklist.objects.filter(project=project)
        .select_related("template", "template__source")
    ):
        total = await ControlQuestion.objects.filter(template=cl.template).acount()
        out.append(await _checklist_out_with_submissions(cl, total))
    return out


@router.get("/{pid}/controls/{checklist_pid}", response=ProjectChecklistDetailOut)
async def get_checklist(request, pid: uuid.UUID, checklist_pid: uuid.UUID):
    project = await _project_for(request, pid)
    cl = await ProjectChecklist.objects.filter(project=project, pid=checklist_pid)\
        .select_related("template", "template__source").afirst()
    if cl is None:
        raise HttpError(404, "no such checklist in this project")
    questions = [
        ControlQuestionOut(order=q.order, text=q.text, article=q.article, category=q.category)
        async for q in ControlQuestion.objects.filter(template=cl.template).order_by("order")
    ]
    total = len(questions)
    base = _checklist_out(cl, total).model_dump()
    base["questions"] = questions
    return ProjectChecklistDetailOut(**base)


@router.delete("/{pid}/controls/{checklist_pid}", response={204: None})
async def uninstall_checklist(request, pid: uuid.UUID, checklist_pid: uuid.UUID):
    project = await _project_for(request, pid, needed="editor")
    cl = await ProjectChecklist.objects.filter(project=project, pid=checklist_pid).afirst()
    if cl is None:
        raise HttpError(404, "no such checklist in this project")
    await cl.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="control",
        resource_id=str(checklist_pid), metadata={"projectPid": str(pid)})
    return 204, None


@router.post("/{pid}/controls", response=ProjectChecklistDetailOut)
async def create_custom_checklist(request, pid: uuid.UUID, data: CustomChecklistIn):
    """Add the project's own checklist: a name plus free-text questions. It lives
    in this project's library like a catalogue install, but under a generated
    template id (never shown in the shared catalogue)."""
    project = await _project_for(request, pid, needed="editor")
    name = data.name.strip()
    if not name:
        raise HttpError(400, "checklist name is required")
    questions = [q for q in data.questions if q.text.strip()]
    if not questions:
        raise HttpError(400, "add at least one question")
    source, _ = await ControlSource.objects.aget_or_create(
        slug="custom", defaults={"name": "Custom", "citation": "", "url": ""}
    )
    template = await ControlTemplate.objects.acreate(
        catalogue_id=f"custom-{uuid.uuid4()}",
        name=name,
        description="",
        source=source,
        control_topic=data.control_topic or "",
    )
    for i, q in enumerate(questions, start=1):
        await ControlQuestion.objects.acreate(
            template=template,
            order=i,
            text=q.text.strip(),
            article=q.article,
            category=q.category,
        )
    cl = await ProjectChecklist.objects.acreate(
        project=project,
        template=template,
        name=name,
        description="",
        control_topic=data.control_topic or "",
    )
    out = _checklist_out(cl, len(questions)).model_dump()
    out["questions"] = [
        ControlQuestionOut(
            order=q.order, text=q.text, article=q.article, category=q.category
        )
        async for q in ControlQuestion.objects.filter(template=template).order_by("order")
    ]
    await sync_to_async(log_action)(
        request, action="create", resource_type="control",
        resource_id=str(cl.pid), metadata={"projectPid": str(pid), "name": name})
    return ProjectChecklistDetailOut(**out)


# --- submissions: the draft -> closed lifecycle ----------------------------------


@router.get("/{pid}/controls/{checklist_pid}/submissions", response=list[SubmissionOut])
async def list_submissions(request, pid: uuid.UUID, checklist_pid: uuid.UUID):
    project = await _project_for(request, pid)
    cl = await ProjectChecklist.objects.filter(project=project, pid=checklist_pid).afirst()
    if cl is None:
        raise HttpError(404, "no such checklist in this project")
    return [
        await _submission_out(sub)
        async for sub in Submission.objects
        .filter(checklist=cl)
        .select_related("checklist__template")
        .order_by("-version")
    ]


class SubmissionCreateIn(Schema):
    label: str = ""


@router.post("/{pid}/controls/{checklist_pid}/submissions", response=SubmissionOut)
async def create_submission(request, pid: uuid.UUID, checklist_pid: uuid.UUID,
                            data: SubmissionCreateIn):
    project = await _project_for(request, pid, needed="editor")
    cl = await ProjectChecklist.objects.filter(project=project, pid=checklist_pid).afirst()
    if cl is None:
        raise HttpError(404, "no such checklist in this project")
    sub = await Submission.objects.acreate(
        label=data.label or cl.name, status=SubmissionStatus.Draft,
        checklist=cl, project=project,
    )
    await sync_to_async(log_action)(
        request, action="create", resource_type="submission",
        resource_id=str(sub.pid), metadata={"projectPid": str(pid), "checklist": cl.name})
    return await _submission_out(sub)


@router.get("/{pid}/controls/submissions/{submission_pid}", response=SubmissionDetailOut)
async def get_submission(request, pid: uuid.UUID, submission_pid: uuid.UUID):
    project = await _project_for(request, pid)
    sub = await (
        Submission.objects.filter(project=project, pid=submission_pid)
        .select_related("checklist", "checklist__template")
        .afirst()
    )
    if sub is None:
        raise HttpError(404, "no such submission")
    return await _submission_detail(sub)


@router.post("/{pid}/controls/submissions/{submission_pid}/answers", response=SubmissionDetailOut)
async def save_answers(request, pid: uuid.UUID, submission_pid: uuid.UUID,
                       data: list[SubmissionAnswerIn]):
    project = await _project_for(request, pid, needed="editor")
    sub = await (
        Submission.objects.filter(project=project, pid=submission_pid)
        .select_related("checklist", "checklist__template")
        .afirst()
    )
    if sub is None:
        raise HttpError(404, "no such submission")
    questions_by_order = {
        q.order: q
        async for q in ControlQuestion.objects.filter(template=sub.checklist.template)
    }
    for item in data:
        q = questions_by_order.get(item.question_order)
        if q is None:
            continue
        await SubmissionAnswer.objects.aupdate_or_create(
            submission=sub, question=q,
            defaults={"answer": item.answer, "score": item.score},
        )
    await sync_to_async(log_action)(
        request, action="answer", resource_type="submission",
        resource_id=str(sub.pid), metadata={"projectPid": str(pid), "answers": len(data)})
    return await _submission_detail(sub)


@router.post("/{pid}/controls/submissions/{submission_pid}/close", response=SubmissionOut)
async def close_submission(request, pid: uuid.UUID, submission_pid: uuid.UUID):
    project = await _project_for(request, pid, needed="editor")
    sub = await (
        Submission.objects.filter(project=project, pid=submission_pid)
        .select_related("checklist", "checklist__template")
        .afirst()
    )
    if sub is None:
        raise HttpError(404, "no such submission")
    sub.status = SubmissionStatus.Closed
    await sub.asave()
    await sync_to_async(log_action)(
        request, action="close", resource_type="submission",
        resource_id=str(sub.pid), metadata={"projectPid": str(pid)})
    return await _submission_out(sub)


@router.post("/{pid}/controls/submissions/{submission_pid}/reopen", response=SubmissionOut)
async def reopen_submission(request, pid: uuid.UUID, submission_pid: uuid.UUID):
    """Reopen a closed submission as the next version (the chain is kept)."""
    project = await _project_for(request, pid, needed="editor")
    sub = await (
        Submission.objects.filter(project=project, pid=submission_pid)
        .select_related("checklist", "checklist__template")
        .afirst()
    )
    if sub is None:
        raise HttpError(404, "no such submission")
    await Submission.objects.acreate(
        project=project, checklist=sub.checklist, label=sub.label,
        status=SubmissionStatus.Draft, version=sub.version + 1, previous=sub,
    )
    await sync_to_async(log_action)(
        request, action="reopen", resource_type="submission",
        resource_id=str(sub.pid), metadata={"projectPid": str(pid)})
    return await _submission_out(sub)


# --- the report (PDF) -------------------------------------------------------------


@router.post("/{pid}/controls/submissions/{submission_pid}/report", response=dict)
async def submission_report(request, pid: uuid.UUID, submission_pid: uuid.UUID):
    """Build the report payload for this submission.

    The PDF itself is rendered by the in-repo `controls-pdf` WeasyPrint
    service (§5); this endpoint returns the JSON payload the renderer needs so
    the webapp (or a direct caller) can POST it and stream the PDF. Direct
    render wiring lives in the future UI pass.
    """
    project = await _project_for(request, pid)
    sub = await (
        Submission.objects.filter(project=project, pid=submission_pid)
        .select_related("checklist", "checklist__template")
        .afirst()
    )
    if sub is None:
        raise HttpError(404, "no such submission")
    questions = [
        ControlQuestionOut(order=q.order, text=q.text, article=q.article,
                           category=q.category)
        async for q in ControlQuestion.objects.filter(template=sub.checklist.template)
        .order_by("order")
    ]
    answers = {
        a.question.order: a async for a in SubmissionAnswer.objects.filter(submission=sub)
    }
    payload = {
        "project": pid,
        "checklist_id": sub.checklist.template.catalogue_id,
        "title": sub.checklist.name,
        "status": sub.status, "version": sub.version,
        "label": sub.label,
        "items": [
            {
                "order": q.order, "text": q.text,
                "answer": (a.answer if (a := answers.get(q.order)) else ""),
                "score": (a.score if (a := answers.get(q.order)) else None),
            }
            for q in questions
        ],
    }
    await sync_to_async(log_action)(
        request, action="generate", resource_type="submission_report",
        resource_id=str(sub.pid), metadata={"projectPid": str(pid)})
    return payload
