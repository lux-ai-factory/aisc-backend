"""
Client for the hosted controls catalogue (AI Factory Sandbox Configurator).

The catalogue is NOT copied into the database up front:
  - `list_controls()`  — `GET /catalogue` lists the checklists straight from the
    hosted API, mapped for the surface and cached briefly in-process. Nothing
    is stored for listing.
  - `upsert_template()` — a checklist's rows
    (`ControlSource`/`ControlTemplate`/`ControlQuestion`) are only materialised
    when a project actually installs it.
  - `export_control()` — the surface's export endpoint proxies the online
    package shape, again without storing.
  - `sync_controls_catalogue()` remains as an OPTIONAL pre-warm for offline or
    air-gapped setups (bulk-store everything at once).

Questions are upserted in place by (template, order) and NEVER deleted: a
project's saved answers (`SubmissionAnswer`) FK to a question with CASCADE, so
deleting one would destroy the answer history. The export carries no stable
question id, so (template, order) is the agreed key.
shortcut: questions are keyed by order; if a checklist re-orders or inserts a
question mid-list, old answers pair with the new question at that position.
Upgrade when the catalogue exposes a per-question id.
"""
from __future__ import annotations

import asyncio
import logging
import time

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is optional at import time
    httpx = None

from config.settings import CONTROLS_CATALOG_URL
from aisc_backend.models.control import ControlQuestion, ControlSource, ControlTemplate

logger = logging.getLogger(__name__)

_CATALOG_ROOT = CONTROLS_CATALOG_URL.rstrip("/")

# How long one listed catalogue snapshot is reused before re-fetching.
# Per worker process; short enough that edits upstream show up, long enough
# that opening "Explore" repeatedly does not hammer the host.
_LIST_TTL_SECONDS = 5 * 60

# public for tests to reset between cases
_list_cache: tuple[float, list[dict]] | None = None


class CatalogueUnavailable(RuntimeError):
    """The hosted catalogue refused or could not be reached."""


async def _get_json(url: str) -> object:
    """One API call; loud on failure so a silent partial result is impossible."""
    if httpx is None:
        raise CatalogueUnavailable("httpx is not installed")
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            response = await http.get(url)
    except httpx.HTTPError as exc:
        raise CatalogueUnavailable(
            f"controls catalogue unreachable ({type(exc).__name__})"
        ) from None
    if response.status_code != 200:
        raise CatalogueUnavailable(
            f"controls catalogue answered {response.status_code}"
        )
    return response.json()


def _is_control(item: dict) -> bool:
    """True for a checklist item: no declared type, or a `type: control` tag.

    The catalogue tags a tool with `section: type` -> `slug: control|test|benchmark`;
    an item without a type tag is treated as a control (nothing else to assume).
    """
    types = [t.get("slug") for t in (item.get("tags") or []) if t.get("section") == "type"]
    return not types or "control" in types


#: The sections a dimension is tagged in: the dimension itself, and the
#: sub-dimensions the catalogue hangs under one (`controls_subdim` for a
#: checklist, `testing_subdim` for a test), which name their dimension in
#: `parent_dimension_slug`.
_DIMENSION_SECTIONS = ("dimension", "controls_subdim", "testing_subdim")


def _dimension_slugs(item: dict) -> list[str]:
    """The item's dimension slugs, a sub-dimension counting for its parent.

    Exactly the catalogue's own rule (its site folds a tag's `slug` and its
    `parent_dimension_slug` together), so an item tagged only under
    `purpose-limitation` is still a `privacy-data-governance` item.
    """
    slugs: list[str] = []
    for tag in item.get("tags") or []:
        if (tag or {}).get("section") not in _DIMENSION_SECTIONS:
            continue
        for slug in (tag.get("slug"), tag.get("parent_dimension_slug")):
            if slug and slug not in slugs:
                slugs.append(slug)
    return slugs


def _mapped(item: dict) -> dict:
    """One online list item -> the surface shape: METADATA ONLY.

    The detailed list embeds questions, but only to count them here — the
    questions themselves are NOT carried (or cached) in the catalogue list;
    they are fetched on demand via export/preview or stored on install.
    """
    meta = item.get("metadata") or {}
    source = meta.get("provider") or item.get("provider") or "Unknown"
    topic = meta.get("control_topic") or ""
    country = [c for c in (meta.get("country_ids") or []) if c]
    regulation = [r for r in (meta.get("regulation_ids") or []) if r]
    tags = list(dict.fromkeys(t for t in [topic, source, *country, *regulation] if t))
    return {
        "catalogue_id": item.get("slug"),
        "title": item.get("name") or item.get("slug"),
        "source": source,
        "control_topic": topic,
        "country_ids": country,
        "regulation_ids": regulation,
        "question_count": len(item.get("questions") or []),
        "tags": tags,
        # the catalogue's own dimension tags: what "for this project" filters on
        "dimensions": _dimension_slugs(item),
    }


async def list_controls() -> list[dict]:
    """The hosted checklist catalogue, mapped for the surface, cached briefly."""
    global _list_cache
    now = time.monotonic()
    if _list_cache is not None and now < _list_cache[0]:
        return _list_cache[1]
    listings = await _get_json(f"{_CATALOG_ROOT}/tool/?detailed=true")
    if not isinstance(listings, list):
        raise CatalogueUnavailable("the catalogue list is not a list")
    mapped = [
        _mapped(item)
        for item in listings
        if (item or {}).get("slug") and _is_control(item or {})
    ]
    _list_cache = (now + _LIST_TTL_SECONDS, mapped)
    return mapped


async def export_control(slug: str) -> dict:
    """The online control package in the surface's {meta, questions} shape."""
    export = await _get_json(f"{_CATALOG_ROOT}/control/{slug}/export")
    if not isinstance(export, dict):
        raise CatalogueUnavailable(f"export for {slug!r} is not an object")
    return export


async def upsert_template(slug: str) -> ControlTemplate:
    """Materialise one checklist's rows for an install.

    Fetches that checklist's export (one async call), upserts
    source/template/questions; returns the template; raises
    CatalogueUnavailable when the export is missing or the API refuses.
    Never deletes questions.
    """
    export = await _get_json(f"{_CATALOG_ROOT}/control/{slug}/export")
    if not isinstance(export, dict):
        raise CatalogueUnavailable(f"export for {slug!r} is not an object")
    await _materialize(
        slug, export,
        {"templates": 0, "questions_created": 0, "questions_updated": 0},
    )
    return await ControlTemplate.objects.aget(catalogue_id=slug)


async def sync_controls_catalogue() -> dict:
    """OPTIONAL pre-warm: upsert every checklist up front (offline setups).

    The hosted list carries controls (checklists) and tests/benchmarks; only
    the `type: control` items are synced — the others have no checklist export
    and belong to the plugin catalogue (still served by the plugin loader).
    The exports are fetched for every checklist CONCURRENTLY, so a full sync
    does not take one round-trip per checklist. One bad checklist does not stop
    the rest.

    Returns a summary dict with created/updated counts per kind plus per-slug
    errors.
    """
    summary = {
        "templates": 0,
        "questions_created": 0,
        "questions_updated": 0,
        "errors": [],
    }
    exports = await _fetch_exports(await _control_slugs())
    for slug, export, error in exports:
        if error is not None:
            summary["errors"].append(f"{slug}: {error}")
            continue
        try:
            await _materialize(slug, export, summary)
        except Exception as exc:  # noqa: BLE001 - one bad checklist must not stop the rest
            summary["errors"].append(f"{slug}: {exc}")
            logger.warning("controls catalogue: failed to sync %s: %s", slug, exc)
    return summary


async def _fetch_exports(slugs: list[str]) -> list[tuple[str, dict | None, str | None]]:
    """Every requested checklist's export, fetched concurrently (bounded).

    A failed or malformed export is reported as `(slug, None, error)` instead of
    aborting the batch; the HTTP fetches happen in parallel, the caller's DB
    upserts stay sequential.
    """
    semaphore = asyncio.Semaphore(8)

    async def _one(slug: str) -> tuple[str, dict | None, str | None]:
        async with semaphore:
            try:
                export = await _get_json(f"{_CATALOG_ROOT}/control/{slug}/export")
            except Exception as exc:  # noqa: BLE001 - record and move on
                return slug, None, str(exc)
            if not isinstance(export, dict):
                return slug, None, "export is not an object"
            return slug, export, None

    return await asyncio.gather(*(_one(slug) for slug in slugs))


async def _control_slugs() -> list[str]:
    """The hosted checklists' slugs (skipping test/benchmark items)."""
    listings = await _get_json(f"{_CATALOG_ROOT}/tool/?detailed=true")
    if not isinstance(listings, list):
        raise CatalogueUnavailable("the catalogue list is not a list")
    return [
        slug
        for item in listings
        if (slug := (item or {}).get("slug")) and _is_control(item or {})
    ]


async def _materialize(slug: str, export: dict, summary: dict) -> None:
    """One checklist's export -> one ControlTemplate + its questions."""
    if not isinstance(export, dict):
        raise CatalogueUnavailable(f"export for {slug!r} is not an object")
    meta = export.get("meta") or {}

    source, _ = await ControlSource.objects.aupdate_or_create(
        name=(meta.get("sourceName") or "Unknown").strip(),
        defaults={
            "slug": str(meta.get("sourceName") or "unknown").strip().lower(),
            "citation": meta.get("description") or "",
            "url": meta.get("sourceUrl") or "",
        },
    )
    template, created = await ControlTemplate.objects.aupdate_or_create(
        catalogue_id=slug,
        defaults={
            "name": meta.get("title") or slug,
            "description": meta.get("description") or "",
            "source": source,
            "control_topic": meta.get("controlTopic") or "",
            "country_ids": meta.get("countryIds") or [],
            "regulation_ids": meta.get("regulationIds") or [],
            "source_updated_at": str(meta.get("sourceUpdatedAt") or "")[:32],
        },
    )
    if created:
        summary["templates"] += 1

    for order, question in enumerate(export.get("questions") or [], start=1):
        if not isinstance(question, dict) or not question.get("text"):
            continue
        _, q_created = await ControlQuestion.objects.aupdate_or_create(
            template=template,
            order=order,
            defaults={
                "text": question["text"],
                "article": question.get("article") or "",
                "category": question.get("category") or "",
            },
        )
        if q_created:
            summary["questions_created"] += 1
        else:
            summary["questions_updated"] += 1
