"""
The catalogue surface — where checklists and plugins are found and installed.

Backend-owned (§16): the backend holds the catalogue's URL/loader and is the
only caller of the outside world. Screens two things:
  - plugins, listed by the plugin-manager loader (local path / package registry);
  - controls (compliance checklists), read through to the hosted catalogue when
    the local tables are empty (see services.controls_catalogue), materialised
    into the control tables on demand.
Search/filter/availability live here; a project installs from here.

Install paths (project-scoped):
  POST /projects/{pid}/catalogue/controls/{catalogue_id}/install
      -> copies the seeded template into the project's checklist library
      (idempotent) or installs a plugin into the engine's loader when the item
      is a plugin package;
  GET  /projects/{pid}/catalogue/control/{slug}/export
      -> the package shape the reference controls app's /api/install consumed
      ({meta, questions}), kept for tooling compatibility.
"""
import logging
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import role_or_404
from aisc_backend.models.control import (
    ControlTemplate,
    ProjectChecklist,
)
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.routers.plugin import plugin_loader
from aisc_backend.schemas.control import (
    CatalogueControlOut,
    CatalogueOut,
    CataloguePluginOut,
)
from aisc_backend.services import controls_catalogue

logger = logging.getLogger(__name__)

router = Router(tags=["catalogue"])

project_repository = ProjectRepository()


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


@router.get("/catalogue", response=CatalogueOut)
async def catalogue(request, kind: str = "all"):
    """Everything installable: plugins (expanded per base-plugin class, from the
    loader) + control checklists (listed from the hosted catalogue, briefly
    cached; nothing is stored until a checklist is installed).

    `kind` narrows the listing to `plugin`, `control` or `all` (the default), so
    one combined UI can ask for both kinds at once without a second endpoint.
    """
    if kind not in ("all", "plugin", "control"):
        raise HttpError(400, "kind must be one of all, plugin, control")
    # Read-through: listing comes from the hosted catalogue, so a fresh DB has a
    # full catalogue with no seed and no sync step.
    controls: list[CatalogueControlOut] = []
    if kind in ("all", "control"):
        try:
            controls = [
                CatalogueControlOut(**item)
                for item in await controls_catalogue.list_controls()
            ]
        except controls_catalogue.CatalogueUnavailable as exc:
            logger.warning("controls catalogue unreachable; listing plugins only: %s", exc)
    plugins = []
    if kind in ("all", "plugin"):
        packages_dict = plugin_loader.list_packages(refresh=True)
        for pkg_name, versions_dict in packages_dict.items():
            for version, meta in versions_dict.items():
                source = meta.get("source", "unknown")
                if source == "local":
                    try:
                        instances = plugin_loader.load_package(pkg_name, version)
                    except Exception:  # noqa: BLE001 - a broken package is listed package-level
                        instances = {}
                    if instances:
                        for cls_name, inst in instances.items():
                            plugins.append(CataloguePluginOut(
                                package_name=pkg_name, plugin_name=cls_name,
                                display_name=getattr(inst, "display_name", None) or cls_name,
                                display_icon=getattr(inst, "display_icon", "") or "",
                                version=version, source=source,
                            ))
                        continue
                plugins.append(CataloguePluginOut(
                    package_name=pkg_name, plugin_name="", display_name="",
                    version=version, source=source,
                ))
    return CatalogueOut(
        plugins=plugins,
        controls=controls,
        app_catalog_url=getattr(settings, "APP_CATALOG_URL", "") or "",
    )


@router.post("/projects/{pid}/catalogue/installs", response=dict)
async def install_from_catalogue(request, pid: uuid.UUID, catalogue_id: str,
                                 kind: str = "control", plugin_name: str = ""):
    """Install a catalogue item into this project.

    `kind=control`: copy the seeded template into the project's checklist
    library (idempotent). `kind=plugin`: installs the requested base-plugin
    class only (`plugin_name`); without one, the package's classes are all
    installed. Pinned to the latest discovered version so configuration and
    loading work immediately.
    """
    project = await _project_for(request, pid, needed="editor")

    if kind == "plugin":
        from packaging.version import parse as parse_version
        from aisc_backend.repositories.plugin_repository import PluginRepository
        from aisc_backend.models.plugin import Plugin

        versions = plugin_loader.list_packages().get(catalogue_id, {})
        if not versions:
            raise HttpError(404, f"no such plugin package {catalogue_id!r}")
        version = str(max(versions.keys(), key=parse_version))
        package_plugins = plugin_loader.load_package(catalogue_id, version)

        targets = [
            (name, inst)
            for name, inst in package_plugins.items()
            if not plugin_name or name == plugin_name
        ]
        if not targets:
            raise HttpError(404, f"no plugin {plugin_name!r} in package {catalogue_id!r}")

        repo = PluginRepository()
        created = []
        for name, inst in targets:
            existing = await repo.find_by_identity(project, catalogue_id, version, name)
            if existing is not None:
                # Reinstall: bring a previously disabled row back instead of
                # creating a duplicate (historical eval data stays attached).
                if not existing.enabled:
                    existing.enabled = True
                    await repo.save(existing)
                created.append(existing)
                continue
            row = await repo.create(
                Plugin(
                    name=name,
                    display_name=getattr(inst, "display_name", None) or name,
                    package_name=catalogue_id,
                    version=version,
                    project=project,
                    enabled=True,
                )
            )
            created.append(row)

        await sync_to_async(log_action)(
            request, action="install", resource_type="plugin",
            resource_id=catalogue_id,
            metadata={"projectPid": str(pid), "kind": "plugin", "catalogue": True,
                      "version": version, "plugins": [p.name for p in created]})
        return {
            "kind": "plugin",
            "catalogue_id": catalogue_id,
            "version": version,
            "plugins": [p.name for p in created],
            "status": "installed",
        }

    template = await ControlTemplate.objects.filter(catalogue_id=catalogue_id).afirst()
    if template is None:
        # Read-through: materialise a checklist from the hosted catalogue that
        # was never synced locally (e.g. first install on a fresh DB).
        try:
            await controls_catalogue.upsert_template(catalogue_id)
        except controls_catalogue.CatalogueUnavailable:
            pass
        template = await ControlTemplate.objects.filter(catalogue_id=catalogue_id).afirst()
    if template is None:
        raise HttpError(404, f"no such control {catalogue_id!r}")
    _checklist, created = await ProjectChecklist.objects.aupdate_or_create(
        project=project, template=template,
        defaults={
            "name": template.name,
            "control_topic": template.control_topic,
            "country_ids": template.country_ids,
            "regulation_ids": template.regulation_ids,
            "source_updated_at": template.source_updated_at,
        },
    )
    await sync_to_async(log_action)(
        request, action="install", resource_type="control",
        resource_id=catalogue_id,
        metadata={"projectPid": str(pid), "kind": "control",
                  "created": created, "checklist": _checklist.name})
    return {"kind": "control", "catalogue_id": catalogue_id,
            "status": "installed" if created else "already-installed"}


@router.get("/projects/{pid}/catalogue/control/{slug}/export", response=dict)
async def export_control_package(request, pid: uuid.UUID, slug: str):
    """The control package in the shape the reference controls app's
    `/api/install` consumed: {meta: {catalogueId,...}, questions: [...]}.

    Proxied from the hosted catalogue (the single source of truth), so an
    uninstalled checklist is also exportable.
    """
    await _project_for(request, pid)
    try:
        export = await controls_catalogue.export_control(slug)
    except controls_catalogue.CatalogueUnavailable:
        raise HttpError(404, f"no such control {slug!r}")
    await sync_to_async(log_action)(
        request, action="export", resource_type="control",
        resource_id=slug, metadata={"projectPid": str(pid), "kind": "control"})
    return export
