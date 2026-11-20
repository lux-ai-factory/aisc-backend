"""
Assembling the versioned evidence map from the single DB (EVD-01).

The router stays thin; everything the pilot's `platform_service/evidence.py`
decides is decided here, read through the owning apps' ORM models:

- the AI-card versions of the project's system (`SystemVersion`);
- the objectives step 2 selected (`ObjectiveProfile`) plus the catalogue rows,
  their risks (the register) and the computed key objectives
  (`services.control_objectives`);
- the installed tests (`Plugin`) and their runs (`EvaluationPlugin`);
- the installed checklists (`ProjectChecklist`) and their latest answers
  (`Submission` / `SubmissionAnswer`, scored by `services.controls_scoring`);
- the saved links and the review (`models.evidence`).

Version scoping: a link belongs to one version. Only the latest version may
change; an older one is read-only. A version with no links of its own offers the
highest prior version's links to carry forward (never silently).
"""
from __future__ import annotations

from aisc_controls.models import ProjectChecklist, Submission, SubmissionAnswer
from aisc_control_objectives.models import ControlObjective, ObjectiveProfile, Risk, RiskObjectiveMapping
from aisc_evaluation.models import EvaluationStatus, EvaluationPlugin
from aisc_control_objectives.models import EvidenceLink, EvidenceReview
from aisc_plugins.models import Plugin
from aisc_backend.models.project import Project
from aisc_backend.models.system import System, SystemVersion
from aisc_backend.services import control_objectives as priorities
from aisc_backend.services.dimensions import dimension_of
from aisc_backend.services import controls_scoring
from aisc_backend.services import objective_profiles

KINDS = ("test", "control")

# The requirement -> dimension slug table lives in `services.dimensions`, its one
# home: the catalogue surface and the objective filters read it too.


class EvidenceError(ValueError):
    """A refused write; the message says which link and why."""


async def _versions(project: Project) -> list[SystemVersion]:
    system = await System.objects.filter(project=project).afirst()
    if system is None:
        return []
    return [v async for v in SystemVersion.objects.filter(system=system).order_by("-number")]


async def _pick_version(
    versions: list[SystemVersion], version_pid: str | None
) -> SystemVersion | None:
    if version_pid is None:
        return versions[0] if versions else None
    wanted = str(version_pid)
    for v in versions:
        if str(v.pid) == wanted:
            return v
    raise LookupError(f"no AI card version {version_pid} in this project")


async def _run_counts(project: Project) -> dict[str, dict]:
    """package_name -> {"executed","failed","running","latest_failed"} over the project's runs.

    Runs are not version-linked on this branch (the report snapshot reads them the
    same way): they count for the project, not one card version. Archived and
    pending rows follow the engine's per-plugin rows.
    """
    out: dict[str, dict] = {}
    rows = (
        EvaluationPlugin.objects.filter(
            evaluation__project=project,
            plugin_config__isnull=False,
        )
        .exclude(evaluation__status=EvaluationStatus.Archived)
        .select_related("plugin_config__plugin", "evaluation")
        .order_by("-evaluation__created_at", "-id")
    )
    seen_latest: set[str] = set()
    async for ep in rows:
        plugin = ep.plugin_config.plugin if ep.plugin_config else None
        if plugin is None:
            continue
        package = plugin.package_name
        bucket = out.setdefault(
            package, {"executed": 0, "failed": 0, "running": 0, "latest_failed": False}
        )
        if ep.status == "Done":
            bucket["executed"] += 1
        elif ep.status == "Failed":
            bucket["failed"] += 1
        elif ep.status in ("Pending", "Running"):
            bucket["running"] += 1
        if package not in seen_latest and ep.status in ("Done", "Failed"):
            bucket["latest_failed"] = ep.status == "Failed"
            seen_latest.add(package)
    return out


async def _submissions(project: Project) -> dict[str, dict]:
    """checklist pid -> latest answers {"answered","questions","readiness"}, non-archived."""
    out: dict[str, dict] = {}
    checklists = [
        c
        async for c in ProjectChecklist.objects.filter(project=project).select_related("template")
    ]
    for checklist in checklists:
        submission = (
            await Submission.objects.filter(checklist=checklist, archived_at__isnull=True)
            .order_by("-created_at")
            .afirst()
        )
        if submission is None:
            out[str(checklist.pid)] = None  # type: ignore[assignment]
            continue
        answers = [a async for a in SubmissionAnswer.objects.filter(submission=submission)]
        answered = sum(
            1 for a in answers if a.score is not None or (a.answer or "").strip()
        )
        scores = [a.score for a in answers if a.score is not None]
        out[str(checklist.pid)] = {
            "answered": answered,
            "questions": await checklist.template.questions.acount(),
            "readiness": controls_scoring.readiness_percent(scores),
        }
    return out


async def _tests(project: Project, runs: dict[str, dict]) -> list[dict]:
    """Installed tests, one tile per package (a package can ship several plugins)."""
    by_package: dict[str, dict] = {}
    async for plugin in Plugin.objects.filter(project=project):
        tile = by_package.setdefault(
            plugin.package_name,
            {
                "key": plugin.package_name,
                "label": plugin.display_name or plugin.package_name,
                "dimensions": [],
                "stale": None,
                "runs": {"executed": 0, "failed": 0, "running": 0, "latest_failed": False},
                "_any_enabled": False,
            },
        )
        if plugin.enabled:
            tile["_any_enabled"] = True
    for package, tile in by_package.items():
        if not tile.pop("_any_enabled"):
            tile["stale"] = "disabled"
        tile["runs"] = runs.get(
            package,
            {"executed": 0, "failed": 0, "running": 0, "latest_failed": False},
        )
    return sorted(by_package.values(), key=lambda t: (t["label"].lower(), t["key"]))


async def _controls(project: Project, submissions: dict[str, dict]) -> list[dict]:
    out = []
    async for checklist in ProjectChecklist.objects.filter(project=project).select_related(
        "template"
    ):
        out.append(
            {
                "key": str(checklist.pid),
                "label": checklist.template.catalogue_id,
                "dimensions": [],
                "stale": None,
                "submission": submissions.get(str(checklist.pid)),
            }
        )
    return sorted(out, key=lambda c: (c["label"].lower(), c["key"]))


async def _profile(project: Project) -> ObjectiveProfile | None:
    return await ObjectiveProfile.objects.filter(project=project).afirst()


async def _risks_by_objective(project: Project) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    rows = (
        RiskObjectiveMapping.objects.filter(project=project)
        .select_related("risk", "objective")
        .order_by("risk__position")
    )
    async for m in rows:
        out.setdefault(m.objective.objective_id, []).append(
            {
                "id": m.risk.name,
                "label": m.risk.name,
                "impact": m.risk.impact,
                "likelihood": m.risk.likelihood,
            }
        )
    return out


async def _priority_keys(project: Project) -> dict[str, bool]:
    """objective_id -> whether the matrix marks it key (assessor override or default)."""
    objectives = [o async for o in ControlObjective.objects.filter(project__isnull=True)]
    objectives += [o async for o in ControlObjective.objects.filter(project=project)]
    risks = [r async for r in Risk.objects.filter(project=project).order_by("position")]
    mappings = [
        m
        async for m in RiskObjectiveMapping.objects.filter(project=project).select_related(
            "risk", "objective"
        )
    ]
    by_risk = {str(r.pid): r for r in risks}
    objective_map: dict[str, list[str]] = {}
    for m in mappings:
        objective_map.setdefault(m.objective.objective_id, []).append(str(m.risk.pid))
    prepared = [
        {"id": o.objective_id, "non_binding": False, "sort_key": o.objective_id}
        for o in objectives
    ]
    profile = await _profile(project)
    keys = {kid: True for kid in profile.key_objectives} if profile else {}
    rating_of = lambda rid: by_risk[rid].rating if rid in by_risk else 0  # noqa: E731
    ordered = priorities.prioritise(
        objectives=prepared, mappings=objective_map, rating_of=rating_of, keys=keys
    )
    return {p.objective_id: p.key for p in ordered}


def _serialize_link(link: EvidenceLink) -> dict:
    return {
        "objective_id": link.objective.objective_id,
        "kind": link.kind,
        "key": link.key,
        "note": link.note,
        "created_at": link.created_at.isoformat(),
        "created_by": link.created_by,
    }


def _item_status(kind: str, item: dict | None) -> str:
    """One linked item's contribution: "none", "attention" or "evidence"."""
    if item is None:
        return "none"
    if kind == "test":
        runs = item["runs"]
        if runs["executed"] + runs["failed"] + runs["running"] == 0:
            return "none"
        return "attention" if runs["latest_failed"] else "evidence"
    submission = item.get("submission")
    if submission is None or submission["answered"] == 0:
        return "none"
    if submission["questions"] and submission["answered"] < submission["questions"]:
        return "attention"
    return "evidence"


async def build_map(
    project: Project,
    version_pid: str | None,
    *,
    can_edit: bool,
    caller_subject: str,
) -> dict:
    """The whole map the evidence-map page renders, for one version."""
    versions = await _versions(project)
    version = await _pick_version(versions, version_pid)

    runs = await _run_counts(project)
    submissions = await _submissions(project)
    tests = await _tests(project, runs)
    controls = await _controls(project, submissions)
    profile = await _profile(project)
    risks_by_objective = await _risks_by_objective(project)
    priority_keys = await _priority_keys(project)

    latest = versions[0] if versions else None
    read_only = version is not None and (latest is None or version.pk != latest.pk)
    mode = can_edit and not read_only

    own_links: list[EvidenceLink] = []
    carried_from: int | None = None
    links_out: list[dict] = []
    review = None

    if version is not None:
        own_links = [
            l
            async for l in EvidenceLink.objects.filter(project=project, version=version)
            .select_related("objective")
            .order_by("objective__objective_id", "kind", "key")
        ]
        if not own_links and version.number > 1:
            for prior in [v for v in versions if v.number < version.number]:
                prior_links = [
                    l
                    async for l in EvidenceLink.objects.filter(
                        project=project, version=prior
                    ).select_related("objective")
                ]
                if prior_links:
                    carried_from = prior.number
                    links_out = [_serialize_link(l) for l in prior_links]
                    break
        if carried_from is None:
            links_out = [_serialize_link(l) for l in own_links]
        review_row = await EvidenceReview.objects.filter(version=version).afirst()
        if review_row is not None:
            review = {
                "reviewed_by": review_row.reviewed_by,
                "reviewed_by_name": review_row.reviewed_by_name,
                "reviewed_at": review_row.reviewed_at.isoformat(),
            }

    # The items the map can show: the installed ones plus any a link still names
    # (a disabled plugin is kept as a tile; a removed one is added, marked).
    tests_by_key = {t["key"]: t for t in tests}
    controls_by_key = {c["key"]: c for c in controls}
    named_tokens = [(l.kind, l.key) for l in own_links]
    named_tokens += [(l["kind"], l["key"]) for l in links_out if carried_from is not None]
    for kind, key in named_tokens:
        if kind == "test" and key not in tests_by_key:
            tests.append(
                {
                    "key": key,
                    "label": key,
                    "dimensions": [],
                    "stale": "removed",
                    "runs": {"executed": 0, "failed": 0, "running": 0, "latest_failed": False},
                }
            )
            tests_by_key[key] = tests[-1]
        elif kind == "control" and key not in controls_by_key:
            controls.append(
                {
                    "key": key,
                    "label": key,
                    "dimensions": [],
                    "stale": "deleted",
                    "submission": None,
                }
            )
            controls_by_key[key] = controls[-1]

    def _item(kind: str, key: str) -> dict | None:
        return tests_by_key.get(key) if kind == "test" else controls_by_key.get(key)

    # The objectives shown: step 2's selection plus any objective a saved link names.
    # A project that has not chosen a profile yet is on the default one
    # ("Fairness and oversight"), as the pilot's first assessment is.
    selected = (
        set(profile.objectives) if profile is not None and profile.objectives else set()
    )
    saved_by_objective: dict[str, list[EvidenceLink]] = {}
    for link in own_links:
        saved_by_objective.setdefault(link.objective.objective_id, []).append(link)

    ordered_ids = [o.objective_id async for o in ControlObjective.objects.filter(project__isnull=True)]
    ordered_ids += [o.objective_id async for o in ControlObjective.objects.filter(project=project)]
    visible = {o.objective_id: o async for o in ControlObjective.objects.filter(project__isnull=True)}
    visible.update({o.objective_id: o async for o in ControlObjective.objects.filter(project=project)})
    if not selected:
        selected = set(objective_profiles.default_ids(visible.values()))
    linked_ids = set(saved_by_objective)
    shown_ids = [oid for oid in ordered_ids if oid in selected or oid in linked_ids]

    objectives_out = []
    for oid in shown_ids:
        row = visible.get(oid)
        if row is None:
            continue
        o_links = saved_by_objective.get(oid, [])
        statuses = [_item_status(l.kind, _item(l.kind, l.key)) for l in o_links]
        if not o_links:
            evidence = "not covered"
        elif all(s == "none" for s in statuses):
            evidence = "no evidence"
        elif any(s == "attention" for s in statuses):
            evidence = "attention"
        else:
            evidence = "evidence"
        title = row.text or row.macro_requirement
        objectives_out.append(
            {
                "id": oid,
                "title": title,
                "dimension": dimension_of(row.macro_requirement),
                "expects": row.assessment_mode,
                "priority": bool(priority_keys.get(oid, False)),
                "stale": None if oid in selected else "not selected",
                "linked": bool(o_links),
                "risks": risks_by_objective.get(oid, []),
                "evidence": evidence,
            }
        )

    linked_count = sum(1 for o in objectives_out if o["linked"])
    coverage = {
        "linked": linked_count,
        "objectives": len(objectives_out),
        "gaps": len(objectives_out) - linked_count,
    }

    # Independent review: a reviewer made none of this version's links.
    own_authors = {l.created_by for l in own_links}
    can_review = mode and caller_subject not in own_authors

    return {
        "version": {"pid": version.pid, "number": version.number} if version else None,
        "versions": [{"pid": v.pid, "number": v.number} for v in versions],
        "objectives": objectives_out,
        "tests": tests,
        "controls": controls,
        "links": links_out,
        "coverage": coverage,
        "review": review,
        "can_edit": mode,
        "can_review": can_review,
        "read_only": read_only,
        "carried_from": carried_from,
    }
