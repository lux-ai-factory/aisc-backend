"""
Assembling the report snapshot from the single DB.

A report's snapshot is the point-in-time picture the report documents, so it
can be generated (PDF/HTML) by the headless service and re-read even after the
project changes:
  - the latest card-version ref (and the AI card it produced, for the report's
    card section);
  - controls coverage (installed checklists + their latest submission's
    coverage/readiness);
  - control-objectives priorities (the same computed matrix the webapp reads).

Everything is read on demand; nothing is cached or stored here (the `Report`
row snapshots the result).
"""
from __future__ import annotations

from datetime import datetime, timezone

from aisc_backend.models.control import (
    ControlQuestion,
    ProjectChecklist,
    Submission,
    SubmissionAnswer,
    SubmissionStatus,
)
from aisc_backend.models.control_objective import (    ControlObjective,
    ObjectiveProfile,
    Risk,
    RiskObjectiveMapping,
)
from aisc_backend.models.evaluation import Evaluation
from aisc_backend.models.evidence import EvidenceLink, EvidenceReview
from aisc_backend.models.measure import Measurement
from aisc_backend.models.observation import Observation
from aisc_backend.models.project import Project
from aisc_backend.models.qualification import Qualification, SystemVersion
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_backend.services import control_objectives as priorities
from aisc_backend.services import controls_scoring
from aisc_backend.services import qualification_service
from aisc_backend.services import report_blocks
from aisc_backend.services import report_local_runs

system_repository = SystemRepository()

#: The report sections a generate call (or the project's settings) can pick.
#: The snapshot always assembles every section's data; `sections` records
#: which of them the headless renderer should render.
REPORT_SECTIONS = ("system", "controls", "objectives", "evaluations")


async def build_snapshot(
    project: Project,
    evaluation_pids: list[str] | None = None,
    sections: list[str] | None = None,
    *,
    version_pid: str | None = None,
    layout: dict | None = None,
    period: str = "",
    other_versions: bool = False,
    compare_to: str | None = None,
) -> dict:
    """Assemble the report snapshot for a project (B5: scoped to the picked
    evaluations and sections; REP-01: scoped to a selected published version and
    a layout, with the evidence map's coverage of that version).

    Everything is read from the ORM here, once, at generation time; the caller
    stores the result on the `Report` row and reads it back — nothing is
    recomputed on read, so a report does not change when the project does.
    """
    system = await system_repository.get_or_create(project)
    version = await _select_version(system, version_pid)
    compare_version = await resolve_compare_to(system, version, compare_to)

    wanted = [s for s in (sections or []) if s in REPORT_SECTIONS]
    evaluations = await _evaluations(project, evaluation_pids or [])
    objectives = await _objectives(project)
    coverage = await _coverage(project, version, objectives.get("priorities") or [])
    comparison = await _comparison(
        project, compare_version, coverage, objectives.get("priorities") or []
    )

    return {
        "project": {
            "pid": str(project.pid),
            "name": project.name,
            "description": project.description,
        },
        "system": {
            "name": system.name,
            "version": _version_ref(version),
            "card": await _card_for(project, version),
        },
        "controls": await _controls(project),
        "objectives": objectives,
        "coverage": coverage,
        "comparison": comparison,
        "evaluations": evaluations,
        "layout": _layout_ref(layout),
        "blocks": _blocks(layout),
        "sections": wanted or list(REPORT_SECTIONS),
        "selection": {
            "version_pid": str(version.pid) if version is not None else None,
            "period": period,
            "other_versions": bool(other_versions),
            "compare_to": compare_to,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _select_version(system, version_pid: str | None) -> SystemVersion | None:
    """The published AI-card version a report is of: the named one (which must be
    the project's), else the latest."""
    if version_pid:
        version = await SystemVersion.objects.filter(system=system, pid=version_pid).afirst()
        if version is not None:
            return version
    return await system_repository.latest(system)


def _layout_ref(layout: dict | None) -> dict | None:
    if not layout:
        return None
    return {
        "id": layout.get("id"),
        "name": layout.get("name"),
        "revision": layout.get("revision"),
        "show_index": layout.get("show_index", True),
        "numbering": layout.get("numbering", True),
    }


def _blocks(layout: dict | None) -> list[dict]:
    """The ordered blocks to render: the layout's, else the assessment-report
    built-in (a generate call always prints a sensible document)."""
    if layout and layout.get("blocks"):
        return layout["blocks"]
    default = report_blocks.builtin_layout("builtin-assessment-report")
    return default["blocks"] if default else []


async def _coverage(project: Project, version: SystemVersion | None, matrix: list[dict]) -> list[dict]:
    """The evidence map of one version: each objective, its linked tests and
    checklists, whether it is covered, and who reviewed the map.

    Read from `EvidenceLink`/`EvidenceReview` (the owning models) at generation
    time; the result is frozen on the report. An objective that a saved link
    names but the matrix no longer selects is kept and marked `stale`.
    """
    if version is None:
        return []
    links = [
        link
        async for link in EvidenceLink.objects.filter(project=project, version=version).select_related(
            "objective"
        )
    ]
    grouped: dict[str, dict[str, list[str]]] = {}
    for link in links:
        bucket = grouped.setdefault(link.objective.objective_id, {"tests": [], "controls": []})
        bucket["tests" if link.kind == "test" else "controls"].append(link.key)
    review = await EvidenceReview.objects.filter(project=project, version=version).afirst()
    reviewed_by = None
    if review is not None:
        reviewed_by = review.reviewed_by_name or review.reviewed_by

    rows: list[dict] = []
    known: set = set()
    for p in matrix:
        oid = p.get("objective_id")
        known.add(oid)
        bucket = grouped.get(oid)
        rows.append({
            "objective_id": oid,
            "text": p.get("text", ""),
            "key": bool(p.get("key")),
            "priority": p.get("score"),
            "rank": p.get("rank"),
            "linked": bool(bucket and (bucket["tests"] or bucket["controls"])),
            "stale": False,
            "tests": (bucket or {}).get("tests", []),
            "controls": (bucket or {}).get("controls", []),
            "reviewed_by": reviewed_by,
        })
    for oid, bucket in grouped.items():
        if oid in known:
            continue
        rows.append({
            "objective_id": oid,
            "text": "",
            "key": False,
            "priority": None,
            "rank": None,
            "linked": True,
            "stale": True,
            "tests": bucket["tests"],
            "controls": bucket["controls"],
            "reviewed_by": reviewed_by,
        })
    return rows


class InvalidComparison(ValueError):
    """The version to compare with is not an earlier version of the same project."""


async def resolve_compare_to(
    system, version: SystemVersion | None, compare_to: str | None
) -> SystemVersion | None:
    """The earlier version a report compares with, or None.

    The pilot's rule (``ScopedData.for_version``): the version to compare with must be a
    version of the same project numbered *below* the pinned one; anything else is refused.
    """
    if not compare_to:
        return None
    other = await SystemVersion.objects.filter(system=system, pid=compare_to).afirst()
    if other is None or version is None or other.number >= version.number:
        raise InvalidComparison(str(compare_to))
    return other


def _cmp_row(row: dict) -> dict:
    return {
        "objective_id": row["objective_id"],
        "text": row.get("text", ""),
        "tests": list(row.get("tests") or []),
        "controls": list(row.get("controls") or []),
    }


async def _comparison(
    project: Project,
    compare_version: SystemVersion | None,
    coverage: list[dict],
    matrix: list[dict],
) -> dict | None:
    """What changed between the compared version and the pinned one (REP-01).

    Both versions' evidence maps are read from the ORM and diffed by objective: an
    objective covered now but not before is `gained`, one covered before but not now is
    `lost`, and for an objective covered in both the evidence keys added and removed are
    listed. `None` when the report compares with nothing.
    """
    if compare_version is None:
        return None
    previous = await _coverage(project, compare_version, matrix)
    now = {r["objective_id"]: r for r in coverage}
    before = {r["objective_id"]: r for r in previous}
    gained: list[dict] = []
    lost: list[dict] = []
    changed: list[dict] = []
    for oid, row in now.items():
        old = before.get(oid)
        if row["linked"] and (old is None or not old["linked"]):
            gained.append(_cmp_row(row))
        elif old is not None and old["linked"] and not row["linked"]:
            lost.append(_cmp_row(old))
        elif old is not None and old["linked"] and row["linked"]:
            added = sorted(
                (set(row["tests"]) | set(row["controls"]))
                - (set(old["tests"]) | set(old["controls"]))
            )
            removed = sorted(
                (set(old["tests"]) | set(old["controls"]))
                - (set(row["tests"]) | set(row["controls"]))
            )
            if added or removed:
                changed.append(
                    {**_cmp_row(row), "added": added, "removed": removed}
                )
    return {
        "version": _version_ref(compare_version),
        "gained": gained,
        "lost": lost,
        "changed": changed,
        "counts": {
            "gained": len(gained),
            "lost": len(lost),
            "changed": len(changed),
        },
    }


def _version_ref(version: SystemVersion | None) -> dict | None:
    if version is None:
        return None
    return {
        "pid": str(version.pid),
        "number": version.number,
        "name": version.name,
        "version": version.version,
        "provider": version.provider,
        "description": version.description,
        "created_at": version.created_at.isoformat(),
        "created_by": version.created_by,
    }


async def _card_for(project: Project, version: SystemVersion | None) -> dict | None:
    """The AI card of the report's version, if any. Prefers the qualification
    that produced that version; falls back to the latest saved card. Also carries
    the JSON-LD graph (what the webapp's card graph is drawn from), so the report
    can render the AI-card graph, not just its summary."""
    q = None
    if version is not None:
        q = (
            await Qualification.objects.filter(project=project, system_version=version)
            .order_by("-created_at")
            .afirst()
        )
    if q is None or not q.system_card:
        q = (
            await Qualification.objects.filter(project=project)
            .order_by("-created_at")
            .afirst()
        )
    if q is None or not q.system_card:
        return None
    card = dict(q.system_card)
    card["jsonld"] = qualification_service.card_jsonld(q.system_card)
    return card


async def _measurement_rows(evaluation: Evaluation, local_names: frozenset[str] = frozenset()) -> list[dict]:
    """The latest observation's raw measurements (metric, score, dimensions).

    Observations of a local-model run (matched by the plugin name the engine writes as
    ``Observation.tool``) are left out: a run on a local model is not part of the
    assessment record (see ``report_local_runs``)."""
    obs = (
        await Observation.objects.filter(evaluation=evaluation)
        .exclude(tool__in=list(local_names))
        .order_by("-created_at")
        .afirst()
    )
    if obs is None:
        return []
    return [
        {
            "metric": m.metric.name,
            "score": m.score,
            "dimensions": m.dimensions if isinstance(m.dimensions, dict) else {},
        }
        async for m in Measurement.objects.filter(observation=obs)
        .select_related("metric")
        .order_by("metric__name", "time")
    ]


#: how many dimension groupings / groups per grouping a report shows
DETAIL_DIMENSION_LIMIT = 3
DETAIL_GROUP_LIMIT = 8


def _evaluation_stats(rows: list[dict]) -> dict | None:
    """Key results + detailed measurements of one evaluation's latest run.

    Key results: headline numbers (observations, mean score, per-metric
    means). Detailed measurements: for the dimensions that appear most
    (category, language, ...), the average score per group — the same
    "byX" breakdown the reference's evaluation plugins report.
    """
    if not rows:
        return None
    scores = [r["score"] for r in rows]
    by_metric: dict[str, list[float]] = {}
    for r in rows:
        by_metric.setdefault(r["metric"], []).append(r["score"])
    metrics = [
        {
            "name": name,
            "mean": round(sum(v) / len(v), 2),
            "min": min(v),
            "max": max(v),
            "n": len(v),
        }
        for name, v in by_metric.items()
    ]
    key_counts: dict[str, int] = {}
    for r in rows:
        for key, value in (r["dimensions"] or {}).items():
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                key_counts[key] = key_counts.get(key, 0) + 1
    dimensions = []
    for key in sorted(key_counts, key=lambda k: -key_counts[k])[:DETAIL_DIMENSION_LIMIT]:
        groups: dict[str, list[float]] = {}
        for r in rows:
            value = (r["dimensions"] or {}).get(key)
            if not isinstance(value, (str, int, float, bool)) or value in ("", None):
                continue
            groups.setdefault(str(value), []).append(r["score"])
        dimension_groups = [
            {
                "label": label,
                "n": len(scores),
                "mean": round(sum(scores) / len(scores), 2),
            }
            for label, scores in sorted(
                groups.items(), key=lambda gi: -len(gi[1])
            )[:DETAIL_GROUP_LIMIT]
        ]
        dimensions.append({"key": key, "groups": dimension_groups})
    return {
        "observations": len(rows),
        "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
        "metrics": metrics,
        "detailed_measurements": dimensions,
    }


async def _evaluations(project: Project, evaluation_pids: list[str]) -> list[dict]:
    if not evaluation_pids:
        return []
    evaluations = [
        e
        async for e in Evaluation.objects.filter(
            project=project, pid__in=evaluation_pids
        ).order_by("created_at")
    ]
    ids = [e.id for e in evaluations]
    # a run on a local model is not part of the assessment record (pilot parity):
    # an evaluation whose runs are all local is left out, one that also has a
    # non-local run stays with the non-local run's data alone.
    only_local = await report_local_runs.only_local_evaluation_ids(ids)
    local_tags = frozenset(await report_local_runs.local_tool_tags(ids))
    result = []
    for e in evaluations:
        if e.id in only_local:
            continue
        result.append(
            {
                "pid": str(e.pid),
                "status": e.status,
                "task": str(e.task) if e.task is not None else None,
                "created_at": e.created_at.isoformat(),
                "key_results": _evaluation_stats(await _measurement_rows(e, local_tags)),
            }
        )
    return result


async def _controls(project: Project) -> list[dict]:
    """Installed checklists with their latest submission's coverage/readiness."""
    rows: list[dict] = []
    checklists = [
        cl
        async for cl in ProjectChecklist.objects.filter(project=project)
        .select_related("template")
        .order_by("name")
    ]
    for cl in checklists:
        total = await ControlQuestion.objects.filter(template=cl.template).acount()
        sub = (
            await Submission.objects.filter(
                checklist=cl, status=SubmissionStatus.Closed
            )
            .order_by("-closed_at", "-created_at")
            .afirst()
        )
        coverage = controls_scoring.coverage(0, total)
        readiness = None
        version = 0
        status = "Not started"
        mean_score = None
        answers = []
        if sub is not None:
            answered = (
                await SubmissionAnswer.objects.filter(
                    submission=sub, score__isnull=False
                ).acount()
            )
            scores = [
                a.score
                async for a in SubmissionAnswer.objects.filter(submission=sub).only(
                    "score"
                )
            ]
            coverage = controls_scoring.coverage(answered, total)
            readiness = controls_scoring.readiness_percent(scores)
            version = sub.version
            status = sub.status
            filled = [s for s in scores if s is not None]
            if filled:
                mean_score = round(sum(filled) / len(filled), 2)
            answers = [
                {
                    "order": a.question.order,
                    "text": a.question.text,
                    "answer": a.answer,
                    "score": a.score,
                }
                async for a in SubmissionAnswer.objects.filter(submission=sub)
                .select_related("question")
                .order_by("question__order")
            ][:1000]  # shortcut: cap like the reference block (MAX_ROWS)
        rows.append(
            {
                "pid": str(cl.pid),
                "name": cl.name,
                "catalogue_id": cl.template.catalogue_id,
                "control_topic": cl.control_topic or "",
                "status": status,
                "version": version,
                "coverage": coverage,
                "readiness": readiness,
                "mean_score": mean_score,
                "answers": answers,
            }
        )
    return rows


async def _objectives(project: Project) -> dict:
    """The computed matrix: priorities (ranked, with key flags) + key objectives.

    Mirrors the matrix the webapp reads (control-objectives router), using the
    same `prioritise` service so a report's objectives match the project's.
    """
    risks = [r async for r in Risk.objects.filter(project=project).order_by("position")]
    mappings = [
        m
        async for m in RiskObjectiveMapping.objects.filter(project=project)
        .select_related("risk", "objective")
    ]
    objectives = [
        o async for o in ControlObjective.objects.all().order_by("objective_id")
    ]

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
    def rating(risk_id: str) -> int:
        risk = by_risk.get(risk_id)
        return risk.rating if risk is not None else 0

    keys = {}
    profile = await ObjectiveProfile.objects.filter(project=project).afirst()
    if profile is not None:
        keys = {kid: True for kid in profile.key_objectives}

    ordered = priorities.prioritise(
        objectives=prepared, mappings=objective_map, rating_of=rating, keys=keys,
    )
    return {
        "priorities": [
            {
                "objective_id": p.objective_id,
                "score": p.score,
                "top_rating": p.top_rating,
                "rank": p.rank,
                "key": p.key,
                "key_default": p.key_default,
                "risk_ids": p.risk_ids,
                "reasons": p.reasons,
                "text": next(
                    (o["text"] for o in prepared if o["id"] == p.objective_id), ""
                ),
            }
            for p in ordered
        ],
        "key_objectives": [p.objective_id for p in ordered if p.key],
        "risks": [
            {
                "pid": str(r.pid),
                "position": r.position,
                "name": r.name,
                "rating": r.rating,
                "comment": r.comment,
            }
            for r in risks
        ],
    }
