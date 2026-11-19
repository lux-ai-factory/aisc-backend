"""
The card's components and the engine's: which is which, and how it has changed.

The pilot (`docs/appendices/1-qualify.md` §"What it produces") records on every
save which of the engine's components (datasets, models) each of the AI card's
components is, **with a snapshot of each**, so the system page can flag a
component that changed in the engine since, and say which card component has no
engine counterpart ("not in the engine"). A card component keeps a stable key
from one version to the next.

The engine's components here are the project's `AIComponent` rows (datasets,
models, llm, datashape, resource) — the same ones CON-01 offers as connection
targets — so the link is made by name, or by pid when the card names one.
"""
from __future__ import annotations

import hashlib
import json

from aisc_backend.models.ai_system import AIComponent
from aisc_qualification.models import CardComponentLink, Qualification
from aisc_backend.models.project import Project

#: Where a card's components are read from, in order: the questionnaire's
#: components block, then the card payload itself.
BLOCK_KEY = "__blocks"


def _rows_from_blocks(answers: dict) -> list[dict]:
    blocks = (answers or {}).get(BLOCK_KEY)
    if not isinstance(blocks, dict):
        return []
    rows = blocks.get("components")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _rows_from_card(card: dict) -> list[dict]:
    if not isinstance(card, dict):
        return []
    for container in (card, card.get("card")):
        if not isinstance(container, dict):
            continue
        rows = container.get("components")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def card_components(answers: dict | None, card: dict | None) -> list[dict]:
    """The card's components as `{key, name, type, role}` (stable keys)."""
    rows = _rows_from_blocks(answers or {}) or _rows_from_card(card or {})
    out: list[dict] = []
    for row in rows:
        name = str(row.get("name") or row.get("label") or "").strip()
        key = str(row.get("key") or row.get("component_key") or name).strip()
        if not key:
            continue
        out.append({
            "key": key,
            "name": name or key,
            "type": str(row.get("component_type") or row.get("type") or "").strip(),
            "role": str(row.get("role") or "").strip(),
            "engine_pid": str(row.get("component_pid") or row.get("pid") or "").strip(),
        })
    return out


def snapshot_of(component: AIComponent) -> dict:
    """A component as it is now — the few identifying fields a change would show in."""
    return {
        "pid": str(component.pid),
        "name": component.name,
        "component_type": component.component_type,
        "data": component.data or "",
        "file_size": component.file_size,
        "json_value": component.json_value or {},
    }


def digest_of(snapshot: dict) -> str:
    """A stable digest of a snapshot, for "changed since" without storing the row."""
    canonical = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _engine_components(project: Project) -> list[AIComponent]:
    return [
        c
        async for c in AIComponent.objects.filter(
            system__project=project
        ).order_by("name")
    ]


async def link(project: Project, qualification: Qualification) -> list[CardComponentLink]:
    """Record (or refresh) this qualification's card-component links.

    Called on every save: a component the card no longer names loses its link
    (its row is dropped, so the set is the card's current components), and a
    component the engine no longer has keeps the link with an empty snapshot —
    "not in the engine" is information, not an error.
    """
    engine = await _engine_components(project)
    by_pid = {str(c.pid): c for c in engine}
    by_name = {c.name.strip().casefold(): c for c in engine if c.name}

    wanted = card_components(qualification.answers or {}, qualification.system_card or {})
    keep: set[str] = set()
    out: list[CardComponentLink] = []
    for row in wanted:
        keep.add(row["key"])
        component = (
            by_pid.get(row["engine_pid"])
            if row["engine_pid"]
            else by_name.get(row["name"].strip().casefold())
        )
        snapshot = snapshot_of(component) if component is not None else {}
        snapshot_digest = digest_of(snapshot) if snapshot else ""
        link_row, _ = await CardComponentLink.objects.aupdate_or_create(
            qualification=qualification,
            component_key=row["key"],
            defaults={
                "project": project,
                "name": row["name"],
                "component_type": row["type"],
                "role": row["role"],
                "engine_component": component,
                "snapshot": snapshot,
                "snapshot_digest": snapshot_digest,
            },
        )
        out.append(link_row)
    stale = CardComponentLink.objects.filter(qualification=qualification)
    if keep:
        stale = stale.exclude(component_key__in=keep)
    await stale.adelete()
    return out


async def read(project: Project, qualification: Qualification) -> list[dict]:
    """The links with what changed since: `changed`, or `missing` from the engine."""
    links = [
        link_row
        async for link_row in CardComponentLink.objects.filter(
            qualification=qualification
        )
        .select_related("engine_component", "engine_component__system")
        .order_by("name", "component_key")
    ]
    current = {str(c.pid): digest_of(snapshot_of(c)) for c in await _engine_components(project)}
    out: list[dict] = []
    for link_row in links:
        engine_component = link_row.engine_component
        engine_pid = str(engine_component.pid) if engine_component is not None else ""
        now = current.get(engine_pid) if engine_pid else None
        in_engine = engine_component is not None and engine_component.system.project_id == project.pk
        out.append({
            "component_key": link_row.component_key,
            "name": link_row.name,
            "component_type": link_row.component_type,
            "role": link_row.role,
            "engine_component_pid": engine_pid if in_engine else "",
            "snapshot": link_row.snapshot,
            "changed": bool(now) and now != link_row.snapshot_digest,
            "missing": not in_engine,
            "captured_at": link_row.created_at.isoformat(),
        })
    return out
