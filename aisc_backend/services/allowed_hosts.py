"""
Allowed internal hosts (CON-01): which addresses a component may call.

The rule itself lives in ``aisc_plugin_interface.adapters.hosts`` so the backend
and the run door share one implementation (ADR 0009, I3). This module adds the
Django side: the project's stored entries, the stack's floor and the deployment's
own service names, and the ``guard_url`` the component "Test" consults immediately
before every call out — so a change to the list applies to the next call with no
restart.
"""
from __future__ import annotations

from django.conf import settings

from aisc_backend.models.connection import AllowedHost
from aisc_backend.models.project import Project
from aisc_plugin_interface.adapters import hosts

#: Re-exported so callers (and tests) keep one name for the pure helpers.
METADATA_HOSTS = hosts.METADATA_HOSTS
INTERNAL_SUFFIXES = hosts.INTERNAL_SUFFIXES
DOCKER_HOST_NAMES = hosts.DOCKER_HOST_NAMES
hostname_of = hosts.hostname_of
port_of = hosts.port_of
normalize_entry = hosts.normalize_entry
is_loopback = hosts.is_loopback
is_metadata = hosts.is_metadata
is_internal = hosts.is_internal

#: What the deployment runs on (docker-compose service names, plus the platform's
#: own front door). A component never reaches these: they are the platform.
DEFAULT_STACK_HOSTS = (
    "aisc-backend",
    "aisc-webapp",
    "aisc-eval-worker",
    "aisc-eval-flower",
    "caddy",
    "frontend",
    "immudb",
    "keycloak",
    "make_buckets",
    "minio",
    "plugin-downloader",
    "postgres",
    "rabbitmq",
    "redis",
)


def stack_hosts() -> set[str]:
    """This deployment's own service names, lower-cased."""
    configured = getattr(settings, "CONNECTIONS_STACK_HOSTS", None)
    if not configured:
        configured = DEFAULT_STACK_HOSTS
    return {str(host).lower().strip(".") for host in configured if str(host).strip()}


def floor_hosts() -> list[str]:
    """Internal hosts the *stack* allows for every project (`CONNECTIONS_ALLOWED_HOSTS`),
    as the pilot's non-removable `floor` rows."""
    configured = getattr(settings, "CONNECTIONS_ALLOWED_HOSTS", None) or []
    seen: list[str] = []
    for entry in configured:
        normal = normalize_entry(str(entry))
        if normal and normal not in seen:
            seen.append(normal)
    return sorted(seen)


def refusal_for(url: str, *, allowed: set[str], floor: set[str]) -> tuple[str, str] | None:
    """Why a call to `url` is refused, or None when it may go out."""
    return hosts.refusal_for(url, allowed=allowed, floor=floor, stack=stack_hosts())


def is_never_allowed(url: str) -> str | None:
    """The reason a host can never be listed from the page, or None."""
    return hosts.is_never_allowed(url, stack=stack_hosts())


async def project_entries(project: Project) -> list[AllowedHost]:
    rows: list[AllowedHost] = []
    async for row in AllowedHost.objects.filter(project=project).order_by("host"):
        rows.append(row)
    return rows


async def allowed_entries(project: Project) -> set[str]:
    """The project's allowlist, as the keys `refusal_for` matches on."""
    return {row.host for row in await project_entries(project)}


async def guard_url(project: Project, url: str) -> dict | None:
    """The refusal a call out gets, in the shape a component "Test" returns; None to go.

    Read fresh at every call, which is why a change to the list applies to the next
    call with no restart.
    """
    refusal = refusal_for(
        url, allowed=await allowed_entries(project), floor=set(floor_hosts())
    )
    if refusal is None:
        return None
    code, detail = refusal
    return {"ok": False, "error": code, "detail": detail, "latency_ms": 0}


def refuse_never_allowed(url: str) -> None:
    """Refuse at save time only what can never work (loopback, metadata, the
    deployment's own services), so a person is told where they typed it."""
    from ninja.errors import HttpError

    reason = is_never_allowed(url)
    if reason:
        raise HttpError(400, reason)
