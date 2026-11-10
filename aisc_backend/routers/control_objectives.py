"""
Control objectives (the risk & control matrix), under /projects/{pid}/...

Backend-native, project-FK scoped. The matrix is computed on every read by
`services.control_objectives` (band -> score -> key objectives), never stored,
so a changed rating can't leave a stale score. Writes are audited with
projectPid.
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import caller_of, role_or_404
from aisc_backend.models.control_objective import (
    ControlObjective,
    ObjectiveProfile,
    Risk,
    RiskObjectiveMapping,
    RiskVersion,
)
from aisc_backend.models.project import Project
from aisc_backend.models.qualification import Qualification
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.control_objective import (
    ControlObjectiveIn,
    ControlObjectiveOut,
    MatrixOut,
    ObjectivePriorityOut,
    ObjectiveProfileIn,
    ObjectiveProfileOut,
    ObjectiveRatingsIn,
    RiskIn,
    RiskObjectiveMappingIn,
    RiskObjectiveMappingOut,
    RiskOut,
    RiskRatingIn,
    RiskVersionOut,
)
from aisc_backend.services import control_objectives as priorities

router = Router(tags=["control-objectives"])

project_repository = ProjectRepository()


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


def _objective_out(o: ControlObjective) -> ControlObjectiveOut:
    return ControlObjectiveOut(
        id=o.objective_id,
        macro_requirement=o.macro_requirement,
        legal_basis=o.legal_basis,
        sub_requirement_label=o.sub_requirement_label,
        text=o.text,
        assessment_mode=o.assessment_mode,
        standards_grounding=o.standards_grounding,
        grounding_tier_flag=o.grounding_tier_flag,
        custom=o.project_id is not None,
    )


def _risk_out(r: Risk) -> RiskOut:
    return RiskOut(
        pid=r.pid,
        position=r.position,
        name=r.name,
        airo_text=r.airo_text,
        impact=r.impact,
        likelihood=r.likelihood,
        rating=r.rating,
        comment=r.comment,
    )


def _risk_version_out(v: RiskVersion) -> RiskVersionOut:
    # `risk` must be select_related by the caller: traversing the relation
    # then is cached, never a sync DB hit in async code.
    return RiskVersionOut(
        pid=v.pid,
        risk_pid=v.risk.pid,
        number=v.number,
        name=v.name,
        impact=v.impact,
        likelihood=v.likelihood,
        rating=v.rating,
        comment=v.comment,
        airo_text=v.airo_text,
        created_at=v.created_at.isoformat(),
        created_by=v.created_by,
    )


async def _create_risk_version(
    risk: Risk, created_by: str
) -> RiskVersion:
    """Append the next revision of a risk (B1, append-only)."""
    last = await (
        RiskVersion.objects.filter(risk=risk).order_by("-number").afirst()
    )
    return await RiskVersion.objects.acreate(
        risk=risk,
        number=(last.number + 1) if last is not None else 1,
        name=risk.name,
        description="",
        impact=risk.impact,
        likelihood=risk.likelihood,
        comment=risk.comment,
        airo_text=risk.airo_text,
        created_by=created_by,
    )


async def _risk_version_list(risk: Risk) -> list[RiskVersionOut]:
    return [
        _risk_version_out(row)
        async for row in RiskVersion.objects.filter(risk=risk)
        .select_related("risk")
        .order_by("number")
    ]


def _mapping_out(m: RiskObjectiveMapping) -> RiskObjectiveMappingOut:
    return RiskObjectiveMappingOut(
        pid=m.pid,
        risk_pid=m.risk.pid,
        objective_id=m.objective.objective_id,
        quote=m.quote,
        rationale=m.rationale,
        source=m.source,
    )


# --- catalogue (seeded rows plus the project's own) -----------------------------

_CATALOGUE_REQUIRED = ("objective_id", "macro_requirement", "text", "assessment_mode")


def _visible_objectives(project):
    from django.db import models as _models
    return ControlObjective.objects.filter(
        _models.Q(project__isnull=True) | _models.Q(project=project)
    ).order_by("objective_id")


@router.get("/{pid}/control-objectives/catalogue", response=list[ControlObjectiveOut])
async def control_objectives_catalogue(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    return [_objective_out(o) async for o in _visible_objectives(project)]


@router.post("/{pid}/control-objectives/catalogue", response=ControlObjectiveOut)
async def add_catalogue_objective(request, pid: uuid.UUID, data: ControlObjectiveIn):
    """Add the project's own catalogue row (the seeded rows stay read-only)."""
    project = await _project_for(request, pid, needed="editor")
    missing = [k for k in _CATALOGUE_REQUIRED if not (getattr(data, k) or "").strip()]
    if missing:
        raise HttpError(400, f"missing required fields: {', '.join(missing)}")
    if await ControlObjective.objects.filter(objective_id=data.objective_id.strip()).aexists():
        raise HttpError(409, f"objective {data.objective_id.strip()} already exists")
    row = await ControlObjective.objects.acreate(
        project=project,
        objective_id=data.objective_id.strip(),
        macro_requirement=data.macro_requirement.strip(),
        legal_basis=data.legal_basis or "",
        sub_requirement_label=data.sub_requirement_label or "",
        text=data.text or "",
        assessment_mode=data.assessment_mode.strip(),
        target=data.target or "",
        standards_grounding=data.standards_grounding or "",
        grounding_tier_flag=data.grounding_tier_flag or "",
        notes=data.notes or "",
    )
    await sync_to_async(log_action)(
        request, action="create", resource_type="control_objective",
        resource_id=row.objective_id, metadata={"projectPid": str(pid)})
    return _objective_out(row)


@router.put("/{pid}/control-objectives/catalogue/{objective_id}", response=ControlObjectiveOut)
async def edit_catalogue_objective(
    request, pid: uuid.UUID, objective_id: str, data: ControlObjectiveIn,
):
    """Edit the project's own catalogue row; seeded rows are read-only."""
    project = await _project_for(request, pid, needed="editor")
    row = await _visible_objectives(project).filter(objective_id=objective_id).afirst()
    if row is None:
        raise HttpError(404, f"no such objective {objective_id}")
    if row.project_id is None:
        raise HttpError(403, "the seeded catalogue is read-only")
    if data.objective_id.strip() != objective_id and await ControlObjective.objects.filter(
        objective_id=data.objective_id.strip()
    ).aexists():
        raise HttpError(409, f"objective {data.objective_id.strip()} already exists")
    for field in ("objective_id", "macro_requirement", "legal_basis", "sub_requirement_label",
                  "text", "assessment_mode", "target", "standards_grounding",
                  "grounding_tier_flag", "notes"):
        value = getattr(data, field)
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    await row.asave()
    await sync_to_async(log_action)(
        request, action="update", resource_type="control_objective",
        resource_id=row.objective_id, metadata={"projectPid": str(pid)})
    return _objective_out(row)


@router.delete("/{pid}/control-objectives/catalogue/{objective_id}", response={204: None})
async def remove_catalogue_objective(request, pid: uuid.UUID, objective_id: str):
    """Delete the project's own catalogue row (mappings to it go too)."""
    project = await _project_for(request, pid, needed="editor")
    row = await _visible_objectives(project).filter(objective_id=objective_id).afirst()
    if row is None:
        raise HttpError(404, f"no such objective {objective_id}")
    if row.project_id is None:
        raise HttpError(403, "the seeded catalogue is read-only")
    await row.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="control_objective",
        resource_id=objective_id, metadata={"projectPid": str(pid)})
    return 204, None


# --- the project's risk register ------------------------------------------------


@router.get("/{pid}/control-objectives/risks", response=list[RiskOut])
async def list_risks(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    return [
        _risk_out(r)
        async for r in Risk.objects.filter(project=project).order_by("position")
    ]


@router.post("/{pid}/control-objectives/risks", response=RiskOut)
async def add_risk(request, pid: uuid.UUID, data: RiskIn):
    project = await _project_for(request, pid, needed="editor")
    position = (
        (await Risk.objects.filter(project=project).order_by("-position").afirst()).position + 1
    ) if await Risk.objects.filter(project=project).aexists() else 1
    risk = await Risk.objects.acreate(
        project=project, position=position,
        name=data.name, airo_text=data.airo_text,
        impact=data.impact, likelihood=data.likelihood, comment=data.comment,
    )
    await _create_risk_version(risk, caller_of(request).subject)
    await sync_to_async(log_action)(
        request, action="create", resource_type="control_risk",
        resource_id=str(risk.pid), metadata={"projectPid": str(pid), "name": risk.name})
    return _risk_out(risk)


@router.post("/{pid}/control-objectives/risks/from-card", response=dict)
async def import_card_risks(request, pid: uuid.UUID):
    """Populate the risk register from the latest AI card's risks.

    Update-or-create matched by risk name: an existing risk's AIRO chain is
    refreshed from the card, but its ratings (impact/likelihood) and its
    control-objective mappings are left untouched; new card risks are added as
    unrated rows (a person rates them).
    """
    project = await _project_for(request, pid, needed="editor")
    card_q = (
        await Qualification.objects.filter(
            project=project, status="completed", system_card__isnull=False
        )
        .order_by("-created_at")
        .afirst()
    )
    if card_q is None:
        raise HttpError(404, "no AI card qualified for this project yet")
    raw_risks = ((card_q.answers or {}).get("__blocks") or {}).get("risks") or []
    if not isinstance(raw_risks, list) or not raw_risks:
        raise HttpError(400, "the AI card has no risks to import")

    by_name = {risk.name: risk async for risk in Risk.objects.filter(project=project)}
    position = (
        (await Risk.objects.filter(project=project).order_by("-position").afirst()).position + 1
        if await Risk.objects.filter(project=project).aexists()
        else 1
    )
    subject = caller_of(request).subject
    created: list[Risk] = []
    updated: list[Risk] = []
    for item in raw_risks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("risk") or "").strip()
        if not name:
            continue
        chain = " → ".join(
            str(part).strip()
            for part in (item.get("source"), item.get("consequence"))
            if str(part or "").strip()
        )
        row = by_name.get(name)
        if row is not None:
            if row.airo_text != chain:
                row.airo_text = chain
                await row.asave()
                await _create_risk_version(row, subject)
            updated.append(row)
            continue
        row = await Risk.objects.acreate(
            project=project, position=position, name=name, airo_text=chain,
        )
        await _create_risk_version(row, subject)
        position += 1
        created.append(row)
    await sync_to_async(log_action)(
        request, action="update", resource_type="control_risk",
        resource_id="from-card", metadata={"projectPid": str(pid),
                                           "created": len(created), "updated": len(updated)})
    return {"created": [_risk_out(risk) for risk in created],
            "updated": [_risk_out(risk) for risk in updated]}


@router.patch("/{pid}/control-objectives/risks/{risk_pid}/rating", response=RiskOut)
async def rate_risk(request, pid: uuid.UUID, risk_pid: uuid.UUID, data: RiskRatingIn):
    project = await _project_for(request, pid, needed="editor")
    risk = await Risk.objects.filter(project=project, pid=risk_pid).afirst()
    if risk is None:
        raise HttpError(404, "no such risk")
    prev = (risk.impact, risk.likelihood, risk.comment, risk.airo_text)
    risk.impact = data.impact
    risk.likelihood = data.likelihood
    if data.comment:
        risk.comment = data.comment
    await risk.asave()
    await sync_to_async(log_action)(
        request, action="update", resource_type="control_risk",
        resource_id=str(risk.pid),
        metadata={"projectPid": str(pid),
                  "impact": risk.impact, "likelihood": risk.likelihood})
    now = (risk.impact, risk.likelihood, risk.comment, risk.airo_text)
    if prev != now:
        await _create_risk_version(risk, caller_of(request).subject)
    return _risk_out(risk)


@router.get("/{pid}/control-objectives/risks/{risk_pid}/versions",
            response=list[RiskVersionOut])
async def list_risk_versions(request, pid: uuid.UUID, risk_pid: uuid.UUID):
    project = await _project_for(request, pid)
    risk = await Risk.objects.filter(project=project, pid=risk_pid).afirst()
    if risk is None:
        raise HttpError(404, "no such risk")
    return await _risk_version_list(risk)


@router.get("/{pid}/control-objectives/risks/{risk_pid}/versions/{number}",
            response=RiskVersionOut)
async def get_risk_version(
    request, pid: uuid.UUID, risk_pid: uuid.UUID, number: int
):
    project = await _project_for(request, pid)
    risk = await Risk.objects.filter(project=project, pid=risk_pid).afirst()
    if risk is None:
        raise HttpError(404, "no such risk")
    version = await (
        RiskVersion.objects.filter(risk=risk, number=number)
        .select_related("risk")
        .afirst()
    )
    if version is None:
        raise HttpError(404, "no such risk version")
    return _risk_version_out(version)


@router.delete("/{pid}/control-objectives/risks/{risk_pid}", response={204: None})
async def remove_risk(request, pid: uuid.UUID, risk_pid: uuid.UUID):
    """Delete a risk from the register; its objective mappings go with it."""
    project = await _project_for(request, pid, needed="editor")
    risk = await Risk.objects.filter(project=project, pid=risk_pid).afirst()
    if risk is None:
        raise HttpError(404, "no such risk")
    await risk.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="control_risk",
        resource_id=str(risk_pid), metadata={"projectPid": str(pid)})
    return 204, None


@router.get("/{pid}/control-objectives/mappings", response=list[RiskObjectiveMappingOut])
async def list_mappings(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    return [
        _mapping_out(m)
        async for m in RiskObjectiveMapping.objects.filter(project=project)
        .select_related("risk", "objective")
    ]


@router.post("/{pid}/control-objectives/risks/{risk_pid}/mappings",
             response=RiskObjectiveMappingOut)
async def add_mapping(request, pid: uuid.UUID, risk_pid: uuid.UUID, data: RiskObjectiveMappingIn):
    project = await _project_for(request, pid, needed="editor")
    risk = await Risk.objects.filter(project=project, pid=risk_pid).afirst()
    if risk is None:
        raise HttpError(404, "no such risk")
    objective = await _visible_objectives(project).filter(objective_id=data.objective_id).afirst()
    if objective is None:
        raise HttpError(404, f"no such objective {data.objective_id}")
    mapping, _ = await RiskObjectiveMapping.objects.aupdate_or_create(
        project=project, risk=risk, objective=objective,
        defaults={"quote": data.quote, "rationale": data.rationale, "source": "person"},
    )
    await sync_to_async(log_action)(
        request, action="create", resource_type="risk_objective_mapping",
        resource_id=str(mapping.pid),
        metadata={"projectPid": str(pid), "risk": risk.name, "objective": objective.objective_id})
    mapping = await RiskObjectiveMapping.objects.filter(pid=mapping.pid)\
        .select_related("risk", "objective").afirst()
    return _mapping_out(mapping)


@router.delete("/{pid}/control-objectives/mappings/{mapping_pid}", response={204: None})
async def remove_mapping(request, pid: uuid.UUID, mapping_pid: uuid.UUID):
    project = await _project_for(request, pid, needed="editor")
    mapping = await RiskObjectiveMapping.objects.filter(project=project, pid=mapping_pid).afirst()
    if mapping is None:
        raise HttpError(404, "no such mapping")
    await mapping.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="risk_objective_mapping",
        resource_id=str(mapping_pid), metadata={"projectPid": str(pid)})
    return 204, None


# --- the matrix, computed on read -----------------------------------------------


@router.get("/{pid}/control-objectives/matrix", response=MatrixOut)
async def control_objectives_matrix(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    risks = [r async for r in Risk.objects.filter(project=project).order_by("position")]
    mappings = [
        m async for m in RiskObjectiveMapping.objects.filter(project=project)
        .select_related("risk", "objective")
    ]
    objectives = [o async for o in _visible_objectives(project)]

    by_risk = {str(risk.pid): risk for risk in risks}
    objective_map: dict[str, list[str]] = {}
    for m in mappings:
        objective_map.setdefault(m.objective.objective_id, []).append(str(m.risk.pid))

    prepared = [
        {
            "id": o.objective_id,
            "text": o.text,
            "non_binding": o.notes.strip().upper().startswith("VOLUNTARY"),
            "sort_key": o.objective_id,
        }
        for o in objectives
    ]
    rating = lambda risk_id: by_risk.get(risk_id).rating if by_risk.get(risk_id) else 0  # noqa: E731
    keys = {}
    overrides: dict = {}
    profile = await ObjectiveProfile.objects.filter(project=project).afirst()
    if profile is not None:
        keys = {kid: True for kid in profile.key_objectives}
        overrides = profile.objective_ratings or {}

    ordered = priorities.prioritise(
        objectives=prepared, mappings=objective_map, rating_of=rating, keys=keys,
    )
    priority_out = []
    for p in ordered:
        manual = overrides.get(p.objective_id, {})
        priority_out.append(ObjectivePriorityOut(
            objective_id=p.objective_id,
            score=float(manual.get("score", p.score)),
            top_rating=p.top_rating,
            rank=p.rank,
            key=p.key,
            key_default=p.key_default,
            risk_ids=p.risk_ids,
            reasons=p.reasons,
            likelihood=manual.get("likelihood"),
            comment=manual.get("comment", ""),
        ))
    return MatrixOut(
        priorities=priority_out,
        risks=[_risk_out(r) for r in risks],
        mappings=[_mapping_out(m) for m in mappings],
    )


# --- objectives profile ----------------------------------------------------------


@router.get("/{pid}/control-objectives/profile", response=ObjectiveProfileOut | None)
async def get_profile(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    profile = await ObjectiveProfile.objects.filter(project=project).afirst()
    if profile is None:
        return None
    return ObjectiveProfileOut(
        pid=profile.pid, name=profile.name,
        objectives=profile.objectives,
        key_objectives=profile.key_objectives,
        catalogue_digest=profile.catalogue_digest,
        objective_ratings=profile.objective_ratings or {},
    )


@router.put("/{pid}/control-objectives/profile", response=ObjectiveProfileOut)
async def set_profile(request, pid: uuid.UUID, data: ObjectiveProfileIn):
    project = await _project_for(request, pid, needed="editor")
    profile, _ = await ObjectiveProfile.objects.aupdate_or_create(
        project=project, defaults={
            "name": data.name,
            "objectives": data.objectives,
            "key_objectives": data.key_objectives,
        },
    )
    await sync_to_async(log_action)(
        request, action="update", resource_type="objective_profile",
        resource_id=str(profile.pid),
        metadata={"projectPid": str(pid), "name": data.name,
                  "key_objectives": data.key_objectives})
    return ObjectiveProfileOut(
        pid=profile.pid, name=profile.name, objectives=profile.objectives,
        key_objectives=profile.key_objectives, catalogue_digest=profile.catalogue_digest,
        objective_ratings=profile.objective_ratings or {},
    )


@router.put("/{pid}/control-objectives/ratings", response=ObjectiveProfileOut)
async def set_objective_ratings(request, pid: uuid.UUID, data: ObjectiveRatingsIn):
    """Set the assessor's manual score/likelihood for individual objectives.

    Merged with the profile's existing overrides; objectives without an entry
    keep their computed (sum of mitigated risk ratings) score.
    """
    project = await _project_for(request, pid, needed="editor")
    profile, _ = await ObjectiveProfile.objects.aget_or_create(
        project=project, defaults={"name": ""},
    )
    merged = dict(profile.objective_ratings or {})
    for oid, rating in data.ratings.items():
        merged[oid] = {
            "score": max(0.0, min(5.0, float(rating.score))),
            "likelihood": rating.likelihood,
            "comment": rating.comment or "",
        }
    profile.objective_ratings = merged
    await profile.asave()
    await sync_to_async(log_action)(
        request, action="update", resource_type="objective_profile",
        resource_id=str(profile.pid),
        metadata={"projectPid": str(pid), "ratings": sorted(data.ratings.keys())})
    return ObjectiveProfileOut(
        pid=profile.pid, name=profile.name, objectives=profile.objectives,
        key_objectives=profile.key_objectives, catalogue_digest=profile.catalogue_digest,
        objective_ratings=profile.objective_ratings,
    )
