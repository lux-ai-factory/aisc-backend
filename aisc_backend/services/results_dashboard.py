"""
Scoped results and the analytics dashboard bridge (ANA-01).

The pilot's results page is one assessed target's tools: each tool shows how many
of its runs completed, failed or are still running, which helper models those runs
used and how they reached the target, and each opens the external analytics
dashboard already filtered on that target and AI-card version. This port keeps
the same shape but reads it from the **evaluation-owned ORM** — never a
third-party table and never a second DB path — and builds the dashboard links
from a configured base URL (`RESULTS_DASHBOARD_URL`). With no base configured the
bridge is closed and only the scoped run summary is served.

Scoping by AI-card version: the unified app does not record the card version a
run was made against, so a version scopes a run by the window it was the head of
the card — `[this version's created_at, the next version's created_at)`. This is
recorded as a deviation from the pilot's explicit run→version link (see
`docs/refactor-deviations.md`).

A run on a locally served model is not part of the assessment record; the tools
carry the flag (`local`) so the results page can keep it distinct, exactly as the
report renderer already does — and the charts are drawn from the *other* runs
only, so a local model's scores never reach a reported figure.

The charts are the visualizer's own query (`MeasurementRepository`), read through
the scope: a metric's count/mean/least/most and its points over the scoped runs.
The dashboard links are signed and scoped (`services.dashboard_link`), so the
dashboard can tell whose link it is looking at and refuse an edited or stale one.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict

from django.conf import settings

from aisc_backend.models.connection import RunBinding
from aisc_backend.models.evaluation import Evaluation, EvaluationPlugin
from aisc_backend.models.observation import Observation
from aisc_backend.models.plugin import Plugin
from aisc_backend.models.qualification import System, SystemVersion
from aisc_backend.repositories.measurement_repository import MeasurementRepository
from aisc_backend.services import dashboard_bridge, dashboard_link, report_local_runs

#: EvaluationPlugin statuses, grouped the way the pilot's tiles show them.
_DONE = "Done"
_FAILED = "Failed"
_RUNNING = ("Running", "Pending")


def dashboard_scope(
    project_pid,
    version_number: int | None = None,
    tool: str | None = None,
    target: str | None = None,
) -> dict:
    """What a link is about: the project, and the version/target/tool it has chosen."""
    return {
        "project": str(project_pid),
        "version": None if version_number is None else str(version_number),
        "target": str(target) if target else None,
        "tool": str(tool) if tool else None,
    }


def build_dashboard_url(
    project_pid,
    version_number: int | None = None,
    tool: str | None = None,
    target: str | None = None,
) -> str | None:
    """The signed, scoped dashboard URL for this scope; None when the bridge is closed.

    Closed means no base URL *or* no signing key: a link the dashboard cannot
    verify is not a link (see `services.dashboard_link`). Pure URL assembly — the
    dashboard is Superset's own process reading its own store, and nothing here
    queries a table of ours.
    """
    return dashboard_link.build_link(
        settings.RESULTS_DASHBOARD_URL or "",
        dashboard_scope(project_pid, version_number, tool, target),
    )


async def _version_window(project, version_pid):
    """The chosen AI-card version and the time window it was the head of the card.

    Returns `(version_row | None, start | None, end | None)`; a project with no
    card version has no window, so every run is in scope. An asked-for version
    that is not this project's raises LookupError (the router answers 404).
    """
    system = await System.objects.filter(project=project).afirst()
    if system is None:
        return None, None, None
    versions = [
        v async for v in SystemVersion.objects.filter(system=system).order_by("number")
    ]
    if not versions:
        return None, None, None
    if version_pid:
        try:
            wanted = uuid.UUID(str(version_pid))
        except (ValueError, AttributeError, TypeError):
            raise LookupError("no such AI card version in this project")
        chosen = next((v for v in versions if v.pid == wanted), None)
        if chosen is None:
            raise LookupError("no such AI card version in this project")
    else:
        chosen = versions[-1]
    start = chosen.created_at
    later = [v for v in versions if v.number > chosen.number]
    end = later[0].created_at if later else None
    return chosen, start, end


def _blank_tool(name: str, label: str) -> dict:
    return {
        "plugin_name": name,
        "label": label,
        "executed": 0,
        "failed": 0,
        "running": 0,
        "last_run": None,
        "local": False,
        "local_runs": 0,
        "helpers": [],
        "connections": [],
        "observations": 0,
        "_run_ids": [],
        "_evaluation_ids": [],
        "_helper_seen": set(),
        "_connection_seen": set(),
    }


async def scoped_results(project, version_pid=None, target=None) -> dict:
    """The scoped run summary for one AI-card version (and optional target).

    `target` is an `AIComponent` pid: a run is in scope when one of its inputs
    binds that component, so a per-component view shows only that component's
    runs. Without a target every run in the version window is in scope.
    """
    version, start, end = await _version_window(project, version_pid)

    evaluations = Evaluation.objects.filter(project=project)
    if start is not None:
        evaluations = evaluations.filter(created_at__gte=start)
    if end is not None:
        evaluations = evaluations.filter(created_at__lt=end)
    evaluation_ids = [e.id async for e in evaluations]

    runs = EvaluationPlugin.objects.filter(evaluation_id__in=evaluation_ids)
    if target:
        runs = runs.filter(evaluation_inputs__component__pid=target)

    local_run_ids = await report_local_runs.local_run_ids(evaluation_ids)

    tools: "OrderedDict[str, dict]" = OrderedDict()
    async for run in runs.select_related("plugin_config__plugin"):
        plugin = run.plugin_config.plugin if run.plugin_config_id else None
        name = plugin.name if plugin is not None else "unknown"
        entry = tools.setdefault(
            name, _blank_tool(name, plugin.display_name if plugin else name)
        )
        entry["_run_ids"].append(run.id)
        if run.evaluation_id not in entry["_evaluation_ids"]:
            entry["_evaluation_ids"].append(run.evaluation_id)
        if run.status == _DONE:
            entry["executed"] += 1
        elif run.status == _FAILED:
            entry["failed"] += 1
        elif run.status in _RUNNING:
            entry["running"] += 1
        if run.finished_at is not None and (
            entry["last_run"] is None or run.finished_at > entry["last_run"]
        ):
            entry["last_run"] = run.finished_at
        if run.id in local_run_ids:
            entry["local_runs"] += 1

    all_run_ids = [rid for entry in tools.values() for rid in entry["_run_ids"]]
    if all_run_ids:
        async for binding in (
            RunBinding.objects.filter(evaluation_plugin_id__in=all_run_ids)
            .select_related("helper", "connection")
        ):
            for entry in tools.values():
                if binding.evaluation_plugin_id not in entry["_run_ids"]:
                    continue
                if binding.role:
                    helper = binding.helper
                    model = binding.model or (helper.name if helper else "")
                    if model and model not in entry["_helper_seen"]:
                        entry["_helper_seen"].add(model)
                        entry["helpers"].append({"role": binding.role, "model": model})
                else:
                    connection = binding.connection
                    if connection is not None:
                        text = f"{connection.kind} ({connection.name})"
                        if text not in entry["_connection_seen"]:
                            entry["_connection_seen"].add(text)
                            entry["connections"].append(
                                {"name": connection.name, "protocol_name": connection.kind}
                            )
                break

    version_number = version.number if version is not None else None
    target_key = str(target) if target else None

    # The observations behind the tiles, and the metric series the visualizer
    # would draw: the same rows the report and the aggregate endpoint read, and
    # an all-local evaluation is left out of both (it is not assessment record).
    scoped_evaluation_ids = sorted({eid for entry in tools.values() for eid in entry["_evaluation_ids"]})
    local_only = await report_local_runs.only_local_evaluation_ids(scoped_evaluation_ids)
    charted_evaluation_ids = [eid for eid in scoped_evaluation_ids if eid not in local_only]
    observations: dict[int, int] = {}
    if scoped_evaluation_ids:
        async for eid in Observation.objects.filter(
            evaluation_id__in=scoped_evaluation_ids
        ).values_list("evaluation_id", flat=True):
            observations[eid] = observations.get(eid, 0) + 1

    repository = MeasurementRepository()
    charts = []
    if charted_evaluation_ids:
        measurements = await repository.filter_queryset(
            observation__evaluation_id__in=charted_evaluation_ids
        )
        charts = await repository.metric_summary(measurements)

    out_tools = []
    for entry in tools.values():
        run_count = entry["executed"] + entry["failed"] + entry["running"]
        entry["local"] = bool(run_count) and entry["local_runs"] == len(entry["_run_ids"])
        entry["observations"] = sum(
            observations.get(eid, 0)
            for eid in entry["_evaluation_ids"]
            if eid not in local_only
        )
        out_tools.append(
            {
                "plugin_name": entry["plugin_name"],
                "label": entry["label"],
                "executed": entry["executed"],
                "failed": entry["failed"],
                "running": entry["running"],
                "last_run": entry["last_run"].isoformat() if entry["last_run"] else None,
                "local": entry["local"],
                "local_runs": entry["local_runs"],
                "observations": entry["observations"],
                "helpers": entry["helpers"],
                "connections": entry["connections"],
                "dashboard_url": build_dashboard_url(
                    project.pid, version_number, entry["plugin_name"], target_key
                ),
            }
        )

    installed = [
        plugin
        async for plugin in Plugin.objects.filter(project=project, enabled=True)
    ]
    greyed = [
        {"plugin_name": plugin.name, "label": plugin.display_name}
        for plugin in installed
        if plugin.name not in tools
    ]

    scope = dashboard_scope(project.pid, version_number, None, target_key)
    dashboards_url = build_dashboard_url(project.pid, version_number, None, target_key)
    return {
        "version": (
            {"pid": version.pid, "number": version.number} if version else None
        ),
        "target": target_key,
        "dashboards_url": dashboards_url,
        # What the link is about, and whether it is authenticated: a link exists
        # only when it is signed, so a page can say why it has none.
        "dashboard": {
            "url": dashboards_url,
            "authenticated": dashboards_url is not None,
            "bridge_configured": dashboard_bridge.configured(),
            "scope": scope,
        },
        "tools": out_tools,
        "greyed": greyed,
        "charts": charts,
        "chart_scope": {
            "version": version_number,
            "target": target_key,
            "evaluations": len(charted_evaluation_ids),
            "local_models_left_out": len(local_only),
        },
    }
