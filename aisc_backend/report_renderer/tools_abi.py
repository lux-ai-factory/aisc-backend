"""The report-plugin ABI (REP-02): the per-tool renderers of the Test results block.

The ABI is `aisc_report_plugin_interface`, vendored verbatim from the pilot. A tool renderer
declares the tool names it answers to and draws one engine run — a read-only `ToolRun`, no
database session — as an HTML fragment. A tool without a renderer falls back to the generic
measurements table (also the pilot's `report_renderer/tools/generic.py`).

Renderers are found the way the pilot finds them: the `aisc_report.tools` entry points of every
installed distribution, plus the four the backend bundles under `aisc_report_plugin_*`.
"""
from __future__ import annotations

import functools
import importlib
import importlib.metadata as metadata
import logging
from types import SimpleNamespace

from aisc_report_plugin_interface import BaseToolRenderer, ToolRun
from aisc_report_plugin_interface.i18n import fill
from aisc_report_plugin_interface.templating import render_package_template

from aisc_backend.models.artifact import Artifact
from aisc_backend.models.measure import Measurement
from aisc_backend.models.observation import Observation
from aisc_backend.models.plugin import EvaluationInput, EvaluationPlugin

logger = logging.getLogger("report_renderer")

ENTRY_POINT_GROUP = "aisc_report.tools"

#: the tool renderers the backend ships (the pilot ships the same four as plugins)
BUNDLED = (
    ("aisc_report_plugin_langbite", "LANGBITEToolRenderer"),
    ("aisc_report_plugin_mlareject", "MLAREJECTToolRenderer"),
    ("aisc_report_plugin_promptfoo", "PROMPTFOOToolRenderer"),
    ("aisc_report_plugin_strongreject", "STRONGREJECTToolRenderer"),
)

#: the measurements table shows this many rows, then "and N more" (the pilot's generic renderer)
GENERIC_LIMIT = 500


@functools.lru_cache(maxsize=1)
def registry() -> tuple[BaseToolRenderer, ...]:
    """Every tool renderer: the installed `aisc_report.tools` entry points, then the bundled ones."""
    renderers: list[BaseToolRenderer] = []
    try:
        entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:                                                # noqa: BLE001 (any metadata failure)
        entry_points = ()
    for entry_point in entry_points:
        try:
            renderers.append(entry_point.load()())
        except Exception:                                            # noqa: BLE001 (a broken third-party plugin)
            logger.warning("report tool entry point %r could not be loaded", entry_point.name, exc_info=True)
    for module_name, class_name in BUNDLED:
        module = importlib.import_module(module_name)
        renderers.append(getattr(module, class_name)())
    return tuple(renderers)


def renderer_for(plugin_row) -> BaseToolRenderer | None:
    """The renderer that answers to this engine plugin row, or None (the generic table then)."""
    for renderer in registry():
        if renderer.matches(plugin_row):
            return renderer
    return None


def inventory() -> list[dict]:
    """What the ABI offers: each renderer, its contract version and the tool names it answers to."""
    return [
        {
            "renderer": type(renderer).__name__,
            "module": type(renderer).__module__,
            "contract_version": renderer.contract_version,
            "tool_names": list(renderer.tool_names),
        }
        for renderer in registry()
    ]


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
    """The run's section as (html, renderer name): the tool's renderer, else the generic table."""
    renderer = renderer_for(run.tool)
    if renderer is None:
        return render_generic(run, ctx), "generic"
    return renderer.render(run, ctx or make_ctx()), type(renderer).__name__


def render_generic(run: ToolRun, ctx=None) -> str:
    """The measurements table for a tool with no renderer (the pilot's generic.py)."""
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
