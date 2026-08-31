"""Catalogue one-click install endpoint (L4) — trust-gated, idempotent register.

Isolated module: the Catalogue (a separate app, no Keycloak session) calls this
single door to install a ``(package_name, version)`` from a *trusted* index and
attach it to a project so it is usable in evaluation.

Design (see L4 contract):
  * The global ninja API is Keycloak-protected. This one operation OVERRIDES that
    with a static-token auth (``CatalogueInstallTokenAuth``) — every other endpoint
    stays on Keycloak, untouched.
  * Flag off (default) => the endpoint is invisible (404), Platform identical to before.
  * Trust is checked BEFORE any install: ``is_trusted_index`` (403) then
    ``pin_requirement`` (400), reusing the L3 helpers.
  * Registration reuses the exact idempotent get-or-create pattern of
    ``plugin.create_plugins`` (2nd identical call => no duplicate rows).
"""
from __future__ import annotations

import hmac
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.models import Plugin
from aisc_backend.repositories.plugin_repository import PluginRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.plugin import PluginOutSchema
from aisc_backend.services.catalogue_install import is_trusted_index, pin_requirement

# Reuse the very same loader the plugin router configured (same registry, no double
# init). Importing it also keeps the install path identical to create_plugins.
from aisc_backend.routers.plugin import plugin_loader

router = Router(tags=["catalogue"])

plugin_repository = PluginRepository()
project_repository = ProjectRepository()


def _bearer_token(request) -> str | None:
    """Extract the bearer token from the Authorization header, or None."""
    header = request.headers.get("Authorization", "") or ""
    scheme, _, token = header.partition(" ")
    if scheme != "Bearer" or not token:
        return None
    return token


def _token_ok(token: str | None) -> bool:
    """Constant-time check of the presented token against CATALOGUE_INSTALL_TOKEN.

    Read at request time. Empty configured token or missing presented token => never
    grants access. Compared over raw UTF-8 bytes so a non-ASCII token never raises.
    """
    expected = getattr(settings, "CATALOGUE_INSTALL_TOKEN", "") or ""
    if not expected or not token:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


def _allow_request(request):
    """Auth that always passes so THIS operation's body is the sole authority.

    The global ninja API is Keycloak-protected; this overrides it on this one op.
    Crucially, ninja's HttpBearer 401s (before any body code) when the Authorization
    header is absent — which would leak the endpoint's existence while the feature
    flag is off. By always authenticating here, the body runs and enforces, in order:
    flag off -> 404 (invisible, identical-to-before), then token -> 401, then trust.
    """
    return "catalogue-install"


class CatalogueInstallRequest(Schema):
    package_name: str
    version: str
    index_url: str
    project_uuid: uuid.UUID


@router.post("/install", response=list[PluginOutSchema], auth=_allow_request)
async def catalogue_install(request, data: CatalogueInstallRequest):
    # 1) Feature flag: off => endpoint invisible (404), identical-to-before —
    #    returned regardless of whether a token header is present.
    if not getattr(settings, "CATALOGUE_INSTALL_ENABLED", False):
        raise HttpError(404, "Not found")

    # 2) Static-token auth (the op's auth always passes so this body is the sole
    #    authority; see _allow_request). Missing/empty/wrong token => 401.
    if not _token_ok(_bearer_token(request)):
        raise HttpError(401, "Unauthorized")

    # 3) Trusted source (E6): reject anything outside the configured allowlist.
    if not is_trusted_index(data.index_url):
        raise HttpError(403, "Untrusted package index")

    # 3) Frozen version (E6): never install a non-pinned version.
    try:
        pin_requirement(data.package_name, data.version)
    except ValueError as e:
        raise HttpError(400, str(e))

    project = await project_repository.get(data.project_uuid, True)

    # 4) Install from the configured registry and enumerate the plugin classes.
    #    A missing package or a failed build must surface as a clean error, never a 500.
    try:
        plugins_package_dict = plugin_loader.load_package(data.package_name, data.version)
    except KeyError:
        raise HttpError(
            502,
            f"Package '{data.package_name}' (version {data.version}) is not available "
            "from the trusted index.",
        )
    except Exception:
        raise HttpError(
            502,
            f"Could not install '{data.package_name}' (version {data.version}) "
            "from the trusted index.",
        )

    # 5) Idempotent get-or-create per plugin class — same pattern as create_plugins.
    installed_plugins = []
    for plugin_name, plugin_obj in plugins_package_dict.items():
        existing = await sync_to_async(
            lambda: Plugin.objects.filter(
                project=project,
                package_name=data.package_name,
                version=data.version,
                name=plugin_name,
            ).first()
        )()

        if existing is not None:
            project_plugin = existing
        else:
            project_plugin = Plugin(
                name=plugin_name,
                display_name=plugin_obj.display_name,
                package_name=data.package_name,
                version=data.version,
                project=project,
                enabled=True,
            )
            project_plugin = await plugin_repository.create(project_plugin)

        installed_plugins.append(project_plugin)

    # If every matching row was soft-disabled, re-enable them (mirror create_plugins).
    if installed_plugins and all(not p.enabled for p in installed_plugins):
        for p in installed_plugins:
            p.enabled = True
            await sync_to_async(p.save)()

    # 6) Audit.
    await sync_to_async(log_action)(
        request, action="install", resource_type="plugin", resource_id=data.package_name,
        metadata={"version": data.version, "projectPid": str(data.project_uuid),
                  "indexUrl": data.index_url, "source": "catalogue",
                  "plugins": [p.name for p in installed_plugins]})

    return installed_plugins
