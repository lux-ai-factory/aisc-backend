"""
Assembling the report snapshot from the single DB.

A report's snapshot is the point-in-time picture the report documents, so it
can be generated (PDF/HTML) by the headless service and re-read even after the
project changes:
  - the latest card-version ref (and the AI card it produced, for the report's
    card section, with the card's knowledge graph as plain nodes/edges);
  - controls coverage (installed checklists + their latest submission's
    coverage/readiness);
  - control-objectives priorities (the same computed matrix the webapp reads,
    limited to the objectives the project's profile selected);
  - each evaluation's key results, plus the charts its plugins declare
    (`get_metric_visualizations`) with the run's measurements, which the
    webapp exports as MUI chart images.

Everything is read on demand; nothing is cached or stored here (the `Report`
row snapshots the result).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from asgiref.sync import sync_to_async
from django.db.models import Q

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
from aisc_backend.models.measure import Measurement
from aisc_backend.models.observation import Observation
from aisc_backend.models.project import Project
from aisc_backend.models.qualification import Qualification, SystemVersion
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_backend.services import control_objectives as priorities
from aisc_backend.services import controls_scoring
from aisc_backend.services import qualification_service

system_repository = SystemRepository()

logger = logging.getLogger(__name__)

#: The report sections a generate call (or the project's settings) can pick.
#: The snapshot always assembles every section's data; `sections` records
#: which of them the headless renderer should render.
REPORT_SECTIONS = ("system", "controls", "objectives", "evaluations", "dashboard")


async def build_snapshot(
    project: Project,
    evaluation_pids: list[str] | None = None,
    sections: list[str] | None = None,
    dashboard_widgets: list[dict] | None = None,
    evaluation_charts: list[dict] | None = None,
) -> dict:
    """Assemble the report snapshot for a project (B5: scoped to the picked
    evaluations, recording the picked sections for the renderer)."""
    system = await system_repository.get_or_create(project)
    version = await system_repository.latest(system)

    wanted = [s for s in (sections or []) if s in REPORT_SECTIONS]
    evaluations = await _evaluations(project, evaluation_pids or [])
    _attach_evaluation_charts(evaluations, evaluation_charts or [])
    dashboard = await _dashboard_widgets(project, dashboard_widgets)
    # the dashboard chapter follows its widgets: a project whose saved default
    # sections predate the "dashboard" section must still render the charts the
    # user just picked.
    if (dashboard.get("widgets") or []) and "dashboard" not in wanted:
        wanted = [*wanted, "dashboard"]

    return {
        "project": {
            "pid": str(project.pid),
            "name": project.name,
            "description": project.description,
        },
        "system": {
            "name": system.name,
            "version": _version_ref(version),
            "card": await _latest_card(project),
        },
        "controls": await _controls(project),
        "objectives": await _objectives(project),
        "evaluations": evaluations,
        "dashboard": dashboard,
        "sections": wanted or list(REPORT_SECTIONS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
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


async def _latest_card(project: Project) -> dict | None:
    """The AI card of the latest saved qualification, if any. Also carries the
    JSON-LD graph (what the webapp's card graph is drawn from) plus the same
    graph as plain nodes/edges (`graph`), which the report draws without having
    to interpret JSON-LD itself."""
    q = (
        await Qualification.objects.filter(project=project)
        .order_by("-created_at")
        .afirst()
    )
    if q is None or not q.system_card:
        return None
    card = dict(q.system_card)
    card["jsonld"] = qualification_service.card_jsonld(q.system_card)
    card["graph"] = _card_graph(card["jsonld"])
    return card


_IRI_TAIL = re.compile(r"[#/:]([^#/:]+)$")
_QUAL_NS = "https://lux-ai-factory.github.io/qualification/ns#"
_LABEL_KEYS = (
    "http://www.w3.org/2000/01/rdf-schema#label",
    "rdfs:label",
    "label",
)


def _local_name(iri: str) -> str:
    match = _IRI_TAIL.search(iri)
    return match.group(1) if match else iri


def _literal(node: dict, *keys: str) -> str | None:
    """The node's first non-empty string value for the keys (a `@value` is
    unwrapped), like the webapp's firstLiteral."""
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item:
                    return item
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("@value"), str)
                    and item["@value"]
                ):
                    return item["@value"]
    return None


def _card_node_label(node: dict) -> str:
    label = _literal(node, *_LABEL_KEYS)
    if label:
        return label
    question_id = _literal(node, f"{_QUAL_NS}questionId", "qual:questionId")
    text = _literal(node, f"{_QUAL_NS}text", "qual:text")
    if question_id and text:
        return f"answer {question_id}: {' '.join(text.split())}"
    if question_id:
        return f"answer {question_id}"
    if text:
        return " ".join(text.split())
    node_id = str(node.get("@id") or "node")
    if node_id.startswith("_:"):
        return _literal(node, f"{_QUAL_NS}citation", "qual:citation") or "answer"
    return _local_name(node_id)


def _card_node_type(node: dict) -> str:
    kind = node.get("@type")
    if isinstance(kind, list):
        return ",".join(_local_name(str(item)) for item in kind if item)
    return _local_name(str(kind)) if kind else ""


def _card_graph(jsonld: str | None) -> dict:
    """The card's JSON-LD as plain {nodes, edges} for the report (the same
    entities the webapp's card graph shows: nodes carry a label + type, edges
    the object references)."""
    if not isinstance(jsonld, str):
        return {"nodes": [], "edges": []}
    try:
        parsed = json.loads(jsonld)
    except ValueError:
        return {"nodes": [], "edges": []}
    if isinstance(parsed, list):
        entries = [n for n in parsed if isinstance(n, dict)]
    elif isinstance(parsed, dict) and isinstance(parsed.get("@graph"), list):
        entries = [n for n in parsed["@graph"] if isinstance(n, dict)]
    elif isinstance(parsed, dict) and isinstance(parsed.get("@id"), str):
        entries = [parsed]
    else:
        entries = []

    ids = {str(n.get("@id")) for n in entries}
    nodes = [
        {"id": str(n.get("@id") or ""), "label": _card_node_label(n), "type": _card_node_type(n)}
        for n in entries
    ]
    edges = []
    for node in entries:
        source = str(node.get("@id") or "")
        for key, value in node.items():
            if key.startswith("@"):
                continue
            targets = value if isinstance(value, list) else [value]
            for target in targets:
                if (
                    isinstance(target, dict)
                    and isinstance(target.get("@id"), str)
                    and target["@id"] in ids
                    and target["@id"] != source
                ):
                    edges.append({"source": source, "target": target["@id"]})
    return {"nodes": nodes, "edges": edges}


async def _observation_rows(obs: Observation) -> list[dict]:
    """An observation's raw measurements (metric, score, dimensions)."""
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


async def _measurement_rows(evaluation: Evaluation) -> list[dict]:
    """The latest observation's raw measurements."""
    obs = (
        await Observation.objects.filter(evaluation=evaluation)
        .order_by("-created_at")
        .afirst()
    )
    if obs is None:
        return []
    return await _observation_rows(obs)


def _declared_charts(evaluation_plugin) -> list[dict]:
    """The charts the run's plugin declares (its contract tells what to draw)."""
    from aisc_backend.routers.plugin import plugin_loader

    config = evaluation_plugin.plugin_config
    plugin = config.plugin
    obj = plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)
    return [
        chart.model_dump(mode="json")
        for chart in obj.get_metric_visualizations(config.config or {})
    ]


async def _plugin_measurements(evaluation: Evaluation, tool: str) -> list[dict]:
    """The given tool's latest observation rows in the evaluation."""
    obs = (
        await Observation.objects.filter(evaluation=evaluation, tool=tool)
        .order_by("-created_at")
        .afirst()
    )
    if obs is None:
        return []
    return await _observation_rows(obs)


async def _visualizations(evaluation: Evaluation) -> list[dict]:
    """Per plugin of the evaluation: what it declares to chart, and the
    measurements of its latest observation (the charts' source rows)."""
    visualizations = []
    plugins = [
        ep
        async for ep in evaluation.evaluation_plugins.select_related(
            "plugin_config__plugin"
        )
    ]
    for evaluation_plugin in plugins:
        config = evaluation_plugin.plugin_config
        if config is None or config.plugin is None:
            continue
        plugin = config.plugin
        try:
            charts = await sync_to_async(_declared_charts)(evaluation_plugin)
        except Exception:  # a plugin that will not load must not fail the report
            logger.warning("report: could not read %s's declared charts", plugin,
                           exc_info=True)
            continue
        if not charts:
            continue
        visualizations.append(
            {
                "plugin": str(plugin),
                "evaluation_plugin_pid": str(evaluation_plugin.pid),
                "charts": charts,
                "measurements": await _plugin_measurements(evaluation, str(plugin)),
            }
        )
    return visualizations


def _attach_evaluation_charts(evaluations: list[dict], exports: list[dict]) -> None:
    """Associate browser plots using server-owned evaluation and plugin declarations."""
    indexed = {
        (item["evaluation_pid"], item["evaluation_plugin_pid"], item["visualization_index"]): item
        for item in exports
    }
    for evaluation in evaluations:
        plots = []
        for plugin in evaluation.get("visualizations", []):
            for index, chart in enumerate(plugin.get("charts", [])):
                if chart.get("chart_type") == "table":
                    continue
                # positional matching (same order as the webapp exported them):
                # a missing entry or unknown index yields an empty panel list,
                # which the renderer shows as an unavailable message. Arbitrary
                # declaration reordering is NOT solved here; stable visualization
                # identifiers remain a separate contract change.
                key = (evaluation["pid"], plugin["evaluation_plugin_pid"], index)
                exported = indexed.get(key, {})
                panels = [
                    {"image": f"artifact:{panel['artifact_id']}",
                     "label": panel.get("label", "")[:200]}
                    for panel in exported.get("panels", [])
                ]
                plots.append({
                    "title": chart.get("title") or "",
                    "description": chart.get("description") or "",
                    "panels": panels,
                })
        evaluation["plots"] = plots


async def _dashboard_widgets(project: Project, widgets: list[dict] | None) -> dict:
    """The dashboard's collected widgets as the webapp exported them: a chart
    carries its report-owned artifact reference + title + comment; a note its
    text. ``project`` is kept for the signature; nothing is read from the DB."""
    del project
    built = []
    for widget in widgets or []:
        if not isinstance(widget, dict):
            continue
        key = str(widget.get("key") or "")[:256]
        kind = widget.get("kind")
        if kind == "text":
            built.append(
                {"key": key, "kind": "text", "text": (widget.get("text") or "")[:2000]}
            )
        elif kind == "chart":
            artifact_id = widget.get("artifact_id")
            if not artifact_id:
                continue  # no uploaded artifact -> nothing this report could show
            built.append(
                {
                    "key": key,
                    "kind": "chart",
                    "title": (widget.get("title") or "")[:200],
                    "comment": (widget.get("comment") or "")[:2000],
                    "image": f"artifact:{artifact_id}",
                }
            )
    return {"widgets": built}

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
    result = []
    async for e in Evaluation.objects.filter(
        project=project, pid__in=evaluation_pids
    ).order_by("created_at"):
        result.append(
            {
                "pid": str(e.pid),
                "status": e.status,
                "task": str(e.task) if e.task is not None else None,
                "created_at": e.created_at.isoformat(),
                "key_results": _evaluation_stats(await _measurement_rows(e)),
                "visualizations": await _visualizations(e),
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
    """The computed matrix for the project's **selected** objectives: each one
    keeps the score/rank it has in the full matrix (the webapp reads the same
    values), but only the objectives picked in the project's objective profile
    are reported. Sorted catalogue order, like the matrix."""
    risks = [r async for r in Risk.objects.filter(project=project).order_by("position")]
    mappings = [
        m
        async for m in RiskObjectiveMapping.objects.filter(project=project)
        .select_related("risk", "objective")
    ]
    # global catalogue rows plus this project's own; never another project's
    visible = [
        o
        async for o in ControlObjective.objects.filter(
            Q(project__isnull=True) | Q(project=project)
        ).order_by("objective_id")
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
        for o in visible
    ]
    def rating(risk_id: str) -> int:
        risk = by_risk.get(risk_id)
        return risk.rating if risk is not None else 0

    keys = {}
    selected: set[str] = set()
    profile = await ObjectiveProfile.objects.filter(project=project).afirst()
    if profile is not None:
        keys = {kid: True for kid in profile.key_objectives}
        selected = set(profile.objectives or [])

    ordered = priorities.prioritise(
        objectives=prepared, mappings=objective_map, rating_of=rating, keys=keys,
    )
    shown = [p for p in ordered if p.objective_id in selected]
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
            for p in shown
        ],
        "key_objectives": [p.objective_id for p in shown if p.key],
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
