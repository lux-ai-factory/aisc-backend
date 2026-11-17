"""
Connections and adapters, under /projects/{pid}/connections/... (CON-01).

Lists the project's connection components (the one `AIComponent` model) with
their adapter and key presence, and lets an editor choose a component's adapter,
its contract version and the helper role it fills. The key itself stays in the
component's configuration and is never read back here.
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import role_or_404
from aisc_backend.models.ai_system import AIComponent
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.connection import ConnectionListOut, ConnectionOut, ConnectionUpdateIn
from aisc_backend.services import connections

router = Router(tags=["connections"])

project_repository = ProjectRepository()


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


@router.get("/{pid}/connections", response=ConnectionListOut)
async def list_connections(request, pid: uuid.UUID):
    project = await _project_for(request, pid)
    rows = [
        connections.connection_row(c)
        async for c in AIComponent.objects.filter(system__project=project).order_by("name")
    ]
    return {"adapters": list(connections.ADAPTERS), "connections": rows}


@router.put("/{pid}/connections/{component_pid}", response=ConnectionOut)
async def update_connection(
    request, pid: uuid.UUID, component_pid: uuid.UUID, data: ConnectionUpdateIn,
):
    project = await _project_for(request, pid, needed="editor")
    component = await AIComponent.objects.filter(
        pid=component_pid, system__project=project
    ).afirst()
    if component is None:
        raise HttpError(404, "no such connection in this project")
    if data.adapter not in connections.ADAPTERS:
        raise HttpError(400, f"unknown adapter {data.adapter!r}")
    component.adapter = data.adapter
    component.adapter_version = data.adapter_version
    component.helper_role = data.helper_role
    await component.asave()
    await sync_to_async(log_action)(
        request,
        action="update",
        resource_type="connection",
        resource_id=str(component.pid),
        metadata={"projectPid": str(pid), "adapter": component.adapter},
    )
    return connections.connection_row(component)
