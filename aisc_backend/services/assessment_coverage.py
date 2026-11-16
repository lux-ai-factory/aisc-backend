"""
Assessment coverage (B4): which installed checklists and enabled plugins could
cover each control objective.

The mapping is indicative, not evidence: an objective counts a checklist or
plugin as covering it when they share a distinctive keyword (normalised,
length 4+, stopwords dropped) between the objective text and the checklist's
topic/title/id or the plugin's name. A changed title can move an item in or
out — this is a triage signal for the Overview page, never an audit claim.
"""
from __future__ import annotations

import re

from aisc_backend.models.control import ProjectChecklist
from aisc_backend.models.control_objective import (
    ControlObjective,
    ObjectiveProfile,
    Risk,
    RiskObjectiveMapping,
)
from aisc_backend.models.plugin import Plugin
from aisc_backend.models.project import Project
from aisc_backend.services import control_objectives as priorities

STOPWORDS = frozenset(
    "about across after among and are being both each from have into more most "
    "must not only other over such than that their them then there these through "
    "under which while with within your".split()
)


def keywords(*texts: str) -> set[str]:
    words: set[str] = set()
    for text in texts:
        for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
            if len(word) >= 4 and word not in STOPWORDS:
                words.add(word)
    return words


async def assessment_coverage(project: Project) -> dict:
    risks = [r async for r in Risk.objects.filter(project=project).order_by("position")]
    mappings = [
        m
        async for m in RiskObjectiveMapping.objects.filter(project=project).select_related(
            "risk", "objective"
        )
    ]
    objectives = [o async for o in ControlObjective.objects.all().order_by("objective_id")]

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

    profile = await ObjectiveProfile.objects.filter(project=project).afirst()
    keys = {kid: True for kid in profile.key_objectives} if profile is not None else {}
    ordered = {p.objective_id: p for p in priorities.prioritise(
        objectives=prepared, mappings=objective_map, rating_of=rating, keys=keys,
    )}

    checklists = [
        cl
        async for cl in ProjectChecklist.objects.filter(project=project).select_related(
            "template"
        )
    ]
    plugins = [p async for p in Plugin.objects.filter(project=project, enabled=True)]

    rows = []
    for o in objectives:
        needles = keywords(o.text)
        covering_checklists = [
            {
                "pid": str(cl.pid),
                "title": cl.name,
                "catalogue_id": cl.template.catalogue_id,
            }
            for cl in checklists
            if needles
            and not needles.isdisjoint(
                keywords(cl.control_topic, cl.template.name, cl.template.catalogue_id)
            )
        ]
        covering_plugins = [
            {"name": p.name, "display_name": p.display_name}
            for p in plugins
            if needles and not needles.isdisjoint(keywords(p.name, p.display_name, p.package_name))
        ]
        prio = ordered.get(o.objective_id)
        rows.append(
            {
                "objective_id": o.objective_id,
                "key": prio.key if prio is not None else False,
                "score": prio.score if prio is not None else 0,
                "rank": prio.rank if prio is not None else 0,
                "mapped_risks": len(objective_map.get(o.objective_id, [])),
                "checklists": covering_checklists,
                "plugins": covering_plugins,
            }
        )
    key_rows = [r for r in rows if r["key"]]
    return {
        "objectives": rows,
        "key_objectives": len(key_rows),
        "key_covered": len(
            [r for r in key_rows if r["checklists"] or r["plugins"] or r["mapped_risks"]]
        ),
    }
