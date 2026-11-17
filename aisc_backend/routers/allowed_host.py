"""
Allowed internal hosts (CON-01) under `/projects/{pid}/allowed-hosts/...`.

A connection may call any public address; an internal one (a private network, the
Docker host, a machine on the LAN) only when the project lists it here or the stack
allows it for every project. This is a *door* rule, read immediately before every
call out, so a change applies to the next call with no restart — the pilot's promise.

Three classes cannot be listed at all and are refused where they are typed: loopback
(the platform itself), cloud metadata addresses, and this deployment's own services.

Reading takes a viewer; changing takes an editor.
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import caller_of, role_or_404
from aisc_backend.auth.membership import at_least
from aisc_backend.models.connection import AllowedHost
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.allowed_host import (
    AllowedHostIn,
    AllowedHostListOut,
    AllowedHostPutOut,
)
from aisc_backend.services import allowed_hosts as service

router = Router(tags=["connections"])
project_repository = ProjectRepository()

#: What the section tells a person, word for word from the pilot.
RULE = (
    "A connection may call any public address. An internal one (a private network, "
    "the Docker host, a machine on your LAN) is refused unless it is listed here, as a "
    "host or host:port. Changes apply to the next call, with no restart. The services "
    "of this deployment, loopback (the platform itself) and cloud metadata addresses "
    "are never allowed here."
)


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> tuple[Project, str]:
    project = await project_repository.get(pid)
    role = await role_or_404(request, project, needed)
    return project, role


def _row(row: AllowedHost) -> dict:
    return {
        "host": row.host,
        "note": row.note,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
        # an entry that names a class which can never be reached is shown, but marked
        "denied": service.is_never_allowed(row.host),
    }


@router.get("/{pid}/allowed-hosts", response=AllowedHostListOut)
async def list_allowed_hosts(request, pid: uuid.UUID):
    """The internal hosts this project's connections may call, plus the ones the
    deployment allows for every project."""
    project, role = await _project_for(request, pid)
    entries = [_row(row) for row in await service.project_entries(project)]
    return {
        "floor": service.floor_hosts(),
        "entries": entries,
        "can_manage": at_least(role, "editor"),
        "rule": RULE,
    }


@router.put("/{pid}/allowed-hosts/{host}", response=AllowedHostPutOut)
async def put_allowed_host(
    request, pid: uuid.UUID, host: str, data: AllowedHostIn | None = None
):
    """Allow an internal host (or host:port) for this project."""
    project, _ = await _project_for(request, pid, "editor")
    normalized = service.normalize_entry(host)
    if normalized is None:
        raise HttpError(400, "name a host or host:port")
    reason = service.is_never_allowed(normalized)
    if reason:
        raise HttpError(400, reason)

    note = ((data.note if data else None) or "").strip()
    row = await AllowedHost.objects.filter(project=project, host=normalized).afirst()
    if row is None:
        row = AllowedHost(project=project, name=normalized, description="", host=normalized)
    row.note = note
    row.updated_by = caller_of(request).subject
    await row.asave()

    await sync_to_async(log_action)(
        request, action="update", resource_type="allowed_host",
        resource_id=str(row.pid),
        metadata={"projectPid": str(pid), "host": normalized},
    )
    return {
        "host": row.host,
        "note": row.note,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


@router.delete("/{pid}/allowed-hosts/{host}", response={204: None})
async def delete_allowed_host(request, pid: uuid.UUID, host: str):
    """Withdraw an internal host: the next call to it is refused again."""
    project, _ = await _project_for(request, pid, "editor")
    normalized = service.normalize_entry(host)
    row = (
        await AllowedHost.objects.filter(project=project, host=normalized).afirst()
        if normalized
        else None
    )
    if row is None:
        raise HttpError(404, "that host is not listed for this project")
    await row.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="allowed_host",
        resource_id=str(pid), metadata={"projectPid": str(pid), "host": normalized},
    )
    return 204, None
