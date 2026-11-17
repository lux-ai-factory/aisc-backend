"""
Allowed internal hosts (CON-01): which addresses a connection may call.

A connection may call any **public** address. An **internal** one — a private
network, the Docker host, a machine on the LAN — is refused unless the project
lists it (Manage → Components and connections → Allowed internal hosts) or the
stack allows it for every project (`CONNECTIONS_ALLOWED_HOSTS`). The deal is
deliberate: whoever can add a connection could otherwise use the platform as a
pivot into the network it lives in.

Three classes can never be allowed, from any page and any environment variable:

* **loopback** (the platform itself — `localhost`, `127.0.0.0/8`, `::1`),
* **cloud metadata** (`169.254.169.254` and friends — they hand out the node's
  own credentials),
* **this deployment's own services** (`CONNECTIONS_STACK_HOSTS`: the compose
  service names the platform talks to with service credentials).

The check runs at the door, not at the form: `guard_url` is consulted immediately
before every call out, so a change to the list applies to the next call with no
restart. The classification helpers are pure and take no database, so the rules
are testable on their own.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings

from aisc_backend.models.connection import AllowedHost
from aisc_backend.models.project import Project

#: Host names a cloud provider answers metadata on. Never reachable.
METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP / OpenStack
        "169.254.169.253",  # AWS DNS
        "169.254.170.2",  # ECS task metadata
        "100.100.100.200",  # Alibaba Cloud
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "fd00:ec2::254",  # AWS IMDSv2 over IPv6
    }
)

#: Name suffixes that are not on the public internet.
INTERNAL_SUFFIXES = (
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".corp",
    ".lan",
    ".home",
    ".svc",
    ".cluster.local",
)

#: The host the Docker host itself answers on (resolved inside a container).
DOCKER_HOST_NAMES = frozenset({"host.docker.internal", "gateway.docker.internal"})

#: What the deployment runs on (docker-compose service names, plus the platform's
#: own front door). A connection never reaches these: they are the platform.
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


def hostname_of(url: str) -> str:
    """The host of a connection's address, lower-cased; '' when it names none."""
    text = (url or "").strip()
    if not text:
        return ""
    if "//" not in text:
        # tolerate a bare `host:port`, which a person may type into the form
        text = "//" + text.split("/", 1)[0]
    try:
        return (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""


def port_of(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text.split("/", 1)[0]
    try:
        return str(urlsplit(text).port or "")
    except ValueError:
        return ""


def normalize_entry(entry: str) -> str | None:
    """A form entry (`host` or `host:port`, maybe a URL) as a stored key, or None.

    Stored lower-cased and without a scheme or path, so `https://Box.Local:8500/`
    and `box.local:8500` are one entry. A mistyped port is refused rather than
    quietly dropped, because a person who typed a port meant it.
    """
    text = (entry or "").strip().lower()
    if not text:
        return None
    if "//" not in text and "/" in text:
        # a bare `host/path` is not an address; a scheme's path is ignored instead
        return None
    if "//" not in text:
        text = "//" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if not host or not host.strip("."):
        return None
    if any(bad in host for bad in " \t\r\n/@?#"):
        return None
    return f"{host}:{port}" if port else host


def is_loopback(host: str) -> bool:
    name = (host or "").lower()
    if not name:
        return False
    if name in {"localhost", "0.0.0.0", "::", "::1", "[::1]"} or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_metadata(host: str) -> bool:
    name = (host or "").lower()
    return name in METADATA_HOSTS


def is_internal(host: str) -> bool:
    """Is this address off the public internet?"""
    name = (host or "").lower().strip(".")
    if not name:
        return True
    if is_loopback(name) or is_metadata(name):
        return True
    if name in DOCKER_HOST_NAMES:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        # a name: single-label, or one of the suffixes a LAN uses
        if "." not in name:
            return True
        return any(name.endswith(suffix) for suffix in INTERNAL_SUFFIXES)
    return not address.is_global or address.is_private or address.is_link_local


def refusal_for(url: str, *, allowed: set[str], floor: set[str]) -> tuple[str, str] | None:
    """Why a call to `url` is refused, or None when it may go out.

    `allowed` are the project's entries and `floor` the stack's; both are matched on
    `host:port` first, then on the bare host, so listing `box.local:8500` does not
    open every port of `box.local`.
    """
    host = hostname_of(url)
    if not host:
        return ("invalid_url", "the address names no host to call")
    if host in stack_hosts():
        return (
            "deployment_service",
            f"{host} is one of this deployment's own services; a test reaches the "
            "assessed system, never the platform",
        )
    if is_loopback(host):
        return ("loopback", "loopback addresses (the platform itself) can never be called")
    if is_metadata(host):
        return (
            "metadata",
            "cloud metadata addresses can never be called: they hand out the node's "
            "own credentials",
        )
    if not is_internal(host):
        return None
    entry = f"{host}:{port_of(url)}" if port_of(url) else host
    if entry in allowed or host in allowed:
        return None
    if entry in floor or host in floor:
        return None
    return (
        "internal_not_allowed",
        f"the internal host {host} is not allowed for this project; an owner can add "
        "it under Allowed internal hosts",
    )


def is_never_allowed(url: str) -> str | None:
    """The reason a host can never be listed from the page, or None.

    Used when *saving* a connection or an allowlist entry, so a hopeless address is
    refused where it is typed instead of failing later at call time.
    """
    host = hostname_of(url)
    if not host:
        return "the address names no host"
    if host in stack_hosts():
        return f"{host} is one of this deployment's own services and can never be reached"
    if is_loopback(host):
        return "loopback addresses (the platform itself) can never be reached"
    if is_metadata(host):
        return "cloud metadata addresses can never be reached"
    return None


async def project_entries(project: Project) -> list[AllowedHost]:
    rows: list[AllowedHost] = []
    async for row in AllowedHost.objects.filter(project=project).order_by("host"):
        rows.append(row)
    return rows


async def allowed_entries(project: Project) -> set[str]:
    """The project's allowlist, as the keys `refusal_for` matches on."""
    return {row.host for row in await project_entries(project)}


async def guard_url(project: Project, url: str) -> dict | None:
    """The refusal a call out gets, in the shape `try_connection` returns; None to go.

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
