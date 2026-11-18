"""
Connections, adapters and the bindings a run records (CON-01), under
`/projects/{pid}/...`.

- `GET/PUT/DELETE /{pid}/connections[/{name}]` — the project's connections.
- `POST /{pid}/connections/{name}/target` — link it to the component it reaches.
- `POST /{pid}/connections/{name}/test` — one call through the connection's adapter,
  exactly as a test would make it (the pilot's "Try").
- `POST /{pid}/connections/{name}/discover` — read an A2A agent's card.
- `GET/PUT /{pid}/runs/{plugin_pid}/bindings` — which adapter/version and which
  helper a run used, snapshotted.

Reading takes a viewer; changing takes an editor. A key is never in a body out:
`PUT` accepts it, but every response reports only whether one is stored.
"""
import re
import uuid

import httpx
from asgiref.sync import sync_to_async
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from aisc_backend import adapters
from aisc_backend.adapters.base import Secret
from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import role_or_404
from aisc_backend.models.connection import Connection, Helper, RunBinding
from aisc_backend.models.plugin import EvaluationPlugin
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.connection import (
    ConnectionIn,
    ConnectionListOut,
    ConnectionOut,
    ConnectionTargetIn,
    ConnectionTryIn,
    ConnectionTryOut,
    RunBindingIn,
    RunBindingListOut,
    RunBindingOut,
)
from aisc_backend.services import allowed_hosts as hosts
from aisc_backend.services import connection_secrets as secrets
from aisc_backend.services import connections as service

router = Router(tags=["connections"])
project_repository = ProjectRepository()

_NAME = re.compile(r"[a-z0-9][a-z0-9\-]{0,62}")
A2A_VERSIONS = ("1.0", "0.3")


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


async def _connection_or_404(project: Project, name: str) -> Connection:
    connection = await Connection.objects.filter(
        project=project, name=name
    ).select_related("target_component").afirst()
    if connection is None:
        raise HttpError(404, f"no connection {name} in this project")
    return connection


async def _plugin_or_404(project: Project, plugin_pid: uuid.UUID) -> EvaluationPlugin:
    plugin = await EvaluationPlugin.objects.filter(
        pid=plugin_pid, evaluation__project=project
    ).afirst()
    if plugin is None:
        raise HttpError(404, "no such run in this project")
    return plugin


async def _secret_for(connection: Connection) -> Secret:
    key = await secrets.get_secret(
        connection.project_id, secrets.connection_key_name(connection.name)
    )
    headers: dict[str, str] = {}
    for header in await secrets.secret_header_names(connection):
        value = await secrets.get_secret(
            connection.project_id, secrets.connection_header_name(connection.name, header)
        )
        if value is not None:
            headers[header] = value
    return Secret(key=key, headers=headers)


@router.get("/{pid}/connections", response=ConnectionListOut)
async def list_connections(request, pid: uuid.UUID):
    """The project's connections, with the protocols this deployment serves."""
    project = await _project_for(request, pid)
    rows = []
    rows_queryset = Connection.objects.filter(project=project).select_related(
        "target_component"
    ).order_by("name")
    async for connection in rows_queryset:
        rows.append(await service.connection_row(connection))
    return {"adapters": adapters.kinds(), "connections": rows}


@router.put("/{pid}/connections/{name}", response=ConnectionOut)
async def put_connection(request, pid: uuid.UUID, name: str, data: ConnectionIn):
    """Create or edit a connection. The target is set once, when it is made."""
    project = await _project_for(request, pid, "editor")
    if not _NAME.fullmatch(name):
        raise HttpError(400, "a connection name is lower-case letters, digits and hyphens")
    if data.kind not in adapters.ADAPTERS:
        raise HttpError(400, f"unknown protocol {data.kind!r}")
    if data.kind == "a2a" and data.protocol_version and data.protocol_version not in A2A_VERSIONS:
        raise HttpError(400, f"unknown A2A version {data.protocol_version!r}")
    # an address that can never work is refused where it is typed; an internal host
    # that is merely not listed yet is allowed here and refused at call time
    hosts.refuse_never_allowed(data.base_url)

    connection = await Connection.objects.filter(
        project=project, name=name
    ).select_related("target_component").afirst()
    creating = connection is None
    if connection is None:
        connection = Connection(project=project, name=name, description="")
    if creating and data.target:
        target = await service.target_for(project, data.target)
        if target is None:
            raise HttpError(400, "the target names no component of this project")
        connection.target_component = target

    connection.label = data.label or connection.label or name
    connection.kind = data.kind
    connection.base_url = data.base_url
    connection.timeout_s = data.timeout_s or 60
    connection.model = data.model
    connection.supports_tools = bool(data.supports_tools)
    connection.supports_logprobs = bool(data.supports_logprobs)
    connection.protocol_version = data.protocol_version
    connection.path = data.path
    connection.method = data.method
    connection.body_template = data.body_template
    connection.response_path = data.response_path or ""
    connection.refusal = data.refusal
    connection.headers = data.headers or {}
    connection.secret_header = data.secret_header or ""
    connection.settings = data.settings or {}
    await connection.asave()

    if data.secret is not None:
        if data.secret == "":
            await secrets.clear_secret(project.pk, secrets.connection_key_name(name))
        else:
            await secrets.set_secret(project.pk, secrets.connection_key_name(name), data.secret)
    for header, value in (data.secret_headers or {}).items():
        if value == "":
            await secrets.remove_secret_header(connection, header)
        else:
            await secrets.set_secret_header(connection, header, value)

    await sync_to_async(log_action)(
        request, action="update", resource_type="connection",
        resource_id=str(connection.pid),
        metadata={"projectPid": str(pid), "kind": connection.kind},
    )
    return await service.connection_row(connection)


@router.delete("/{pid}/connections/{name}", response={204: None})
async def delete_connection(request, pid: uuid.UUID, name: str):
    project = await _project_for(request, pid, "editor")
    connection = await _connection_or_404(project, name)
    # every secret it stored goes with it
    for header in await secrets.secret_header_names(connection):
        await secrets.remove_secret_header(connection, header)
    await secrets.clear_secret(project.pk, secrets.connection_key_name(name))
    await connection.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="connection",
        resource_id=str(pid), metadata={"projectPid": str(pid), "name": name},
    )
    return 204, None


@router.post("/{pid}/connections/{name}/target", response=ConnectionOut)
async def set_target(request, pid: uuid.UUID, name: str, data: ConnectionTargetIn):
    project = await _project_for(request, pid, "editor")
    connection = await _connection_or_404(project, name)
    target = await service.target_for(project, data.key)
    if target is None:
        raise HttpError(400, "the target names no component of this project")
    connection.target_component = target
    await connection.asave(update_fields=["target_component"])
    await sync_to_async(log_action)(
        request, action="set_target", resource_type="connection",
        resource_id=str(connection.pid),
        metadata={"projectPid": str(pid), "name": name, "target": str(target.pid)},
    )
    return await service.connection_row(connection)


@router.post("/{pid}/connections/{name}/test", response=ConnectionTryOut)
async def test_connection(request, pid: uuid.UUID, name: str, data: ConnectionTryIn | None = None):
    """One call through the adapter, as a test would make it. What comes back is
    what a test gets: the extracted answer, or the system's refusal."""
    project = await _project_for(request, pid)
    connection = await _connection_or_404(project, name)
    # the door: the allowlist is read here, immediately before the call out, so a
    # change to it applies to the next call with no restart
    refusal = await hosts.guard_url(project, connection.base_url)
    if refusal is not None:
        connection.last_test_at = timezone.now()
        connection.last_test_ok = False
        await connection.asave(update_fields=["last_test_at", "last_test_ok"])
        return refusal
    secret = await _secret_for(connection)
    outcome = await adapters.try_connection(
        connection, secret,
        input=(data.input if data else "ping"),
        history=(data.history if data else None),
        params=(data.params if data else None),
    )
    connection.last_test_at = timezone.now()
    connection.last_test_ok = bool(outcome["ok"])
    await connection.asave(update_fields=["last_test_at", "last_test_ok"])
    return outcome


@router.post("/{pid}/connections/{name}/discover", response=dict)
async def discover_connection(request, pid: uuid.UUID, name: str):
    """Read an A2A agent's card: the interface URL it names is the base URL plus
    the connection's path."""
    project = await _project_for(request, pid, "editor")
    connection = await _connection_or_404(project, name)
    if connection.kind != "a2a":
        raise HttpError(400, "only an A2A connection has an agent card")
    refusal = await hosts.guard_url(project, connection.base_url)
    if refusal is not None:
        raise HttpError(403, refusal["detail"])
    try:
        card = await adapters.discover_agent_card(connection)
    except httpx.HTTPError as exc:
        raise HttpError(502, f"the agent's card could not be read: {exc}") from exc
    return {
        "url": adapters.agent_card_url(connection.base_url, connection.protocol_version or "1.0"),
        "name": card.get("name", ""),
        "description": card.get("description", ""),
        "url_named_by_card": card.get("url", ""),
        "protocol_version": connection.protocol_version or "1.0",
        "capabilities": card.get("capabilities", {}),
    }


@router.get("/{pid}/runs/{plugin_pid}/bindings", response=RunBindingListOut)
async def list_bindings(request, pid: uuid.UUID, plugin_pid: uuid.UUID):
    """What the run used: the assessed connection, and the helper per role."""
    project = await _project_for(request, pid)
    plugin = await _plugin_or_404(project, plugin_pid)
    bindings = [
        service.binding_row(binding)
        async for binding in RunBinding.objects.filter(
            evaluation_plugin=plugin
        ).select_related("connection", "helper").order_by("role")
    ]
    return {"bindings": bindings}


@router.put("/{pid}/runs/{plugin_pid}/bindings", response=RunBindingOut)
async def put_binding(request, pid: uuid.UUID, plugin_pid: uuid.UUID, data: RunBindingIn):
    """Bind a connection (role empty) or the helper a role resolves to. The
    adapter/version is snapshotted, so a later edit does not rewrite the run."""
    project = await _project_for(request, pid, "editor")
    plugin = await _plugin_or_404(project, plugin_pid)
    connection = None
    helper = None
    if data.connection:
        connection = await Connection.objects.filter(project=project, name=data.connection).afirst()
        if connection is None:
            raise HttpError(404, f"no connection {data.connection} in this project")
    if data.helper:
        helper = await Helper.objects.filter(project=project, name=data.helper).afirst()
        if helper is None:
            raise HttpError(404, f"no helper {data.helper} in this project")
    if connection is None and helper is None:
        raise HttpError(400, "a binding names a connection or a helper")
    if data.role:
        if data.role not in service.HELPER_ROLES:
            raise HttpError(400, f"unknown role {data.role!r}")
        if helper is None:
            raise HttpError(400, "a role binds a helper")
    binding = await service.bind(plugin, role=data.role, connection=connection, helper=helper)
    await sync_to_async(log_action)(
        request, action="bind", resource_type="run_binding",
        resource_id=str(binding.pid),
        metadata={
            "projectPid": str(pid),
            "role": data.role,
            "connection": connection.name if connection else None,
            "helper": helper.name if helper else None,
            "adapter_version": binding.adapter_version,
        },
    )
    return service.binding_row(binding)
