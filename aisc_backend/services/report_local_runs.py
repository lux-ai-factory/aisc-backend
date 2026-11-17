"""Local-model-only runs are not part of the assessment record (pilot parity).

The pilot's engine marks a run made against a locally served model by storing
``{"local_model": {...}}`` as the value of the plugin's model input, and its report
renderer leaves such runs out of every read (``report_renderer/data/engine.py``):
a run on a local model file is not part of the assessment record, an evaluation
whose runs are *all* local is left out of a selection and refused when asked for by
pid, and an evaluation that also has a non-local run stays with the non-local run
alone.

The unified app does not yet write that marker, so this is a guard that only bites
when the marker appears; it is ported so the reporting rule matches the pilot's.
"""
from __future__ import annotations

from aisc_backend.models.plugin import EvaluationInput, EvaluationPlugin

#: the engine input that marks a run on a locally served model
LOCAL_MODEL_KEY = "local_model"


def is_local_value(value) -> bool:
    """The input value carries the local-model marker (an object, as the engine writes it)."""
    return isinstance(value, dict) and isinstance(value.get(LOCAL_MODEL_KEY), dict)


async def local_run_ids(evaluation_ids: list[int]) -> set[int]:
    """The ids of the evaluation plugins (runs) of these evaluations that are local."""
    if not evaluation_ids:
        return set()
    run_ids = [
        rid
        async for rid in EvaluationPlugin.objects.filter(
            evaluation_id__in=evaluation_ids
        ).values_list("id", flat=True)
    ]
    if not run_ids:
        return set()
    local: set[int] = set()
    async for run_id, value in EvaluationInput.objects.filter(
        evaluation_plugin_id__in=run_ids
    ).values_list("evaluation_plugin_id", "value"):
        if is_local_value(value):
            local.add(run_id)
    return local


async def only_local_evaluation_ids(evaluation_ids: list[int]) -> set[int]:
    """The evaluations of these whose runs exist and are every one local."""
    if not evaluation_ids:
        return set()
    runs: dict[int, list[int]] = {}
    async for rid, eid in EvaluationPlugin.objects.filter(
        evaluation_id__in=evaluation_ids
    ).values_list("id", "evaluation_id"):
        runs.setdefault(eid, []).append(rid)
    local = await local_run_ids(evaluation_ids)
    return {
        eid
        for eid, rids in runs.items()
        if rids and all(rid in local for rid in rids)
    }


async def local_tool_tags(evaluation_ids: list[int]) -> set[str]:
    """The ``Observation.tool`` tags of the local runs on these evaluations.

    ``Observation.tool`` is written as ``str(plugin)`` — ``"{package}::{name} (v{version})"``,
    the engine's plugin tag — so a local run's observations are told apart by that tag.
    """
    if not evaluation_ids:
        return set()
    local = await local_run_ids(evaluation_ids)
    if not local:
        return set()
    tags: set[str] = set()
    async for run in EvaluationPlugin.objects.filter(id__in=list(local)).select_related(
        "plugin_config__plugin"
    ):
        plugin = run.plugin_config.plugin if run.plugin_config is not None else None
        if plugin is not None:
            tags.add(str(plugin))
    return tags
