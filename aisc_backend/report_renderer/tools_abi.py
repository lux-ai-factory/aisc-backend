"""The per-tool section of the Test results block.

Each run of an evaluation is drawn by the eval plugin that produced it: the backend loads the
plugin object (the same way `services/report_snapshot.py` does to learn what to chart) and asks
it for a renderer through `BaseEvaluationPlugin.get_report_renderer()`. The renderer gets a
read-only `ToolRun` — one engine run, no database session — and returns an HTML fragment.

A plugin that does not implement the method, returns None, or whose renderer raises is **not**
left out of the report: it falls back to the generic measurements table
(`render_generic`). That is the default section, so a plugin written before this contract
existed — or one with nothing custom to draw — still renders and exports something useful.
"""
from __future__ import annotations

import functools
import logging
from types import SimpleNamespace

from aisc_plugin_interface.report import BaseToolRenderer, ToolRun
from aisc_plugin_interface.report.i18n import fill
from aisc_plugin_interface.report.templating import render_package_template

from aisc_backend.models.artifact import Artifact
from aisc_backend.models.measure import Measurement
from aisc_backend.models.observation import Observation
from aisc_backend.models.plugin import EvaluationInput, EvaluationPlugin

logger = logging.getLogger("report_renderer")

#: the measurements table shows this many rows, then "and N more" (the pilot's generic renderer)
GENERIC_LIMIT = 500

#: the renderer name reported when a run falls back to the generic table
GENERIC = "generic"


@functools.lru_cache(maxsize=128)
def _plugin_renderer(package_name: str, name: str, version: str) -> BaseToolRenderer | None:
    """The `get_report_renderer()` of the eval plugin, or None when it has none.

    Loading the plugin is the mechanism the report already uses for charts
    (`_declared_charts`), so a plugin's renderer is reached the same way as its
    visualizations — no second registry and no backend-bundled per-tool code.
    Any failure (the plugin is missing, the call raises) degrades to the default section.
    """
    try:
        from aisc_backend.routers.plugin import plugin_loader

        obj = plugin_loader.load_plugin(package_name, name, version)
    except Exception:                                                # noqa: BLE001 (a plugin the backend cannot load)
        logger.warning("report renderer: could not load plugin %s/%s@%s", name, package_name, version,
                       exc_info=True)
        return None
    getter = getattr(obj, "get_report_renderer", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:                                                # noqa: BLE001 (a broken plugin renderer)
        logger.warning("report renderer: %s/%s@%s raised in get_report_renderer", name, package_name,
                       version, exc_info=True)
        return None


def renderer_for(tool) -> BaseToolRenderer | None:
    """The eval plugin's report renderer for this run's tool, or None (the generic table then)."""
    if not tool:
        return None
    package_name = tool.get("package_name")
    if not package_name:
        return None
    return _plugin_renderer(str(package_name), str(tool.get("name") or ""), str(tool.get("version") or ""))


def make_ctx(language: str = "en", tool_options: dict | None = None) -> SimpleNamespace:
    """The block context a tool renderer gets: its tool options, its language and its formatters."""
    return SimpleNamespace(
        language=language,
        tool_options=dict(tool_options or {}),
        t=lambda message, **params: fill(message, params),
        number=lambda value: value,
        decimal_separator=".",
        percent_format="{value}%",
        translate_for=None,
    )


def render_run(run: ToolRun, ctx=None) -> tuple[str, str]:
    """The run's section as (html, renderer name): the plugin's renderer, else the generic table.

    A renderer that raises degrades to the generic table, as in the pilot — one broken plugin
    never costs the report its whole Test results block.
    """
    ctx = ctx or make_ctx()
    renderer = renderer_for(run.tool)
    if renderer is None:
        return render_generic(run, ctx), GENERIC
    try:
        return renderer.render(run, ctx), type(renderer).__name__
    except Exception:                                                # noqa: BLE001 (a broken plugin renderer)
        logger.warning("report renderer %s raised, the generic table is used instead", type(renderer).__name__,
                       exc_info=True)
        return render_generic(run, ctx), GENERIC


def render_generic(run: ToolRun, ctx=None) -> str:
    """The default section for a run whose plugin draws none: its measurements table.

    This is what a plugin without a report renderer gets, so a report never shows an empty
    section for a tool that ran.
    """
    ctx = ctx or make_ctx()
    measurements = list(run.measurements)
    rows = [
        {
            "metric": m.get("metric") or m.get("name"),
            "value": _value(m.get("score")),
            "unit": m.get("unit") or "",
        }
        for m in measurements[:GENERIC_LIMIT]
    ]
    more = max(0, len(measurements) - GENERIC_LIMIT)
    return render_package_template(
        "aisc_backend.report_renderer.tools",
        "templates/generic.html.j2",
        rows=rows,
        more=more,
        has_measurements=bool(measurements),
        t=getattr(ctx, "t", None) or (lambda message, **params: fill(message, params)),
    )


def _value(score) -> str:
    if score is None:
        return ""
    try:
        return f"{float(score):g}"
    except (TypeError, ValueError):
        return str(score)


def _tool_ref(plugin) -> dict:
    if plugin is None:
        return {"name": "", "display_name": "", "package_name": "", "version": ""}
    return {
        "name": plugin.name,
        "display_name": plugin.display_name,
        "package_name": plugin.package_name,
        "version": plugin.version,
    }


async def tool_runs(evaluation) -> list[ToolRun]:
    """One read-only `ToolRun` per plugin run of the evaluation, read from the ORM (I5).

    Runs on a local model are not part of the assessment record and are left out, as everywhere
    else in reporting (`services.report_local_runs`)."""
    from aisc_backend.services import report_local_runs

    local = await report_local_runs.local_run_ids([evaluation.id])
    runs: list[ToolRun] = []
    async for run in EvaluationPlugin.objects.filter(evaluation=evaluation).select_related(
        "plugin_config__plugin"
    ):
        if run.id in local:
            continue
        plugin = run.plugin_config.plugin if run.plugin_config is not None else None
        tag = str(plugin) if plugin is not None else ""
        inputs = [
            inp
            async for inp in EvaluationInput.objects.filter(evaluation_plugin=run).select_related("component")
        ]
        components = tuple(
            {
                "name": inp.component.name,
                "component_type": inp.component.component_type,
                "pid": str(inp.component.pid),
                "input": inp.name,
            }
            for inp in inputs
        )
        observations = tuple(
            [
                {"observer": o.observer, "tool": o.tool, "pid": str(o.pid)}
                async for o in Observation.objects.filter(evaluation=evaluation)
            ]
        )
        measurements = tuple(
            [
                {
                    "name": m.name,
                    "metric": m.metric.name,
                    "score": m.score,
                    "unit": m.unit,
                    "dimensions": m.dimensions,
                    "description": m.description,
                    "direction": m.direction,
                }
                async for m in Measurement.objects.filter(
                    observation__evaluation=evaluation, observation__tool=tag
                ).select_related("metric")
            ]
        )
        artifacts = tuple(
            [
                {"name": a.name, "file_size": a.file_size, "pid": str(a.pid)}
                async for a in Artifact.objects.filter(evaluation_plugin=run)
            ]
        )
        runs.append(
            ToolRun(
                evaluation={"pid": str(evaluation.pid), "status": evaluation.status,
                            "created_at": evaluation.created_at.isoformat()},
                evaluation_plugin={"pid": str(run.pid), "status": run.status},
                tool=_tool_ref(plugin),
                components=components,
                observations=observations,
                measurements=measurements,
                artifacts=artifacts,
                target=None,
            )
        )
    return runs
