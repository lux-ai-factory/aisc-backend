"""
Helper models (CON-01) under `/projects/{pid}/helpers/...`.

A helper is a model a test uses as a tool — a judge, an attacker, a grader, an
embedder, a generator. It is one OpenAI-compatible endpoint, one key (kept in the
project's encrypted secret store), and the roles it serves. `POST .../test` makes
one small call with the stored key and reports the status only.
"""
import re
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend import adapters
from aisc_backend.adapters.base import Secret
from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import role_or_404
from aisc_backend.auth.membership import at_least
from aisc_backend.models.connection import Connection, Helper
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.connection import (
    HelperIn,
    HelperListOut,
    HelperOut,
    HelperTryIn,
)
from aisc_backend.services import connection_secrets as secrets
from aisc_backend.services import connections as service

router = Router(tags=["connections"])
project_repository = ProjectRepository()

_NAME = re.compile(r"[a-z0-9][a-z0-9\-]{0,62}")


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    role = await role_or_404(request, project, needed)
    return project, role


async def _helper_or_404(project: Project, name: str) -> Helper:
    helper = await Helper.objects.filter(project=project, name=name).afirst()
    if helper is None:
        raise HttpError(404, f"no helper {name} in this project")
    return helper


@router.get("/{pid}/helpers", response=HelperListOut)
async def list_helpers(request, pid: uuid.UUID):
    project, role = await _project_for(request, pid)
    helpers = [
        await service.helper_row(helper)
        async for helper in Helper.objects.filter(project=project).order_by("name")
    ]
    return {
        "can_manage": at_least(role, "editor"),
        "roles": list(service.HELPER_ROLES),
        "helpers": helpers,
    }


@router.put("/{pid}/helpers/{name}", response=HelperOut)
async def put_helper(request, pid: uuid.UUID, name: str, data: HelperIn):
    project, _ = await _project_for(request, pid, "editor")
    if not _NAME.fullmatch(name):
        raise HttpError(400, "a helper name is lower-case letters, digits and hyphens")
    unknown = [r for r in (data.roles or []) if r not in service.HELPER_ROLES]
    if unknown:
        raise HttpError(400, f"unknown role(s): {', '.join(unknown)}")
    if not data.endpoint_url:
        raise HttpError(400, "a helper needs an endpoint URL")

    helper = await Helper.objects.filter(project=project, name=name).afirst()
    if helper is None:
        helper = Helper(project=project, name=name, description="")
    helper.label = data.label or helper.label or name
    helper.endpoint_url = data.endpoint_url
    helper.roles = list(data.roles or [])
    await helper.asave()

    if data.key is not None:
        if data.key == "":
            await secrets.clear_secret(project.pk, secrets.helper_key_name(name))
        else:
            await secrets.set_secret(project.pk, secrets.helper_key_name(name), data.key)

    await sync_to_async(log_action)(
        request, action="update", resource_type="helper",
        resource_id=str(helper.pid),
        metadata={"projectPid": str(pid), "roles": helper.roles},
    )
    return await service.helper_row(helper)


@router.delete("/{pid}/helpers/{name}", response={204: None})
async def delete_helper(request, pid: uuid.UUID, name: str):
    project, _ = await _project_for(request, pid, "editor")
    helper = await _helper_or_404(project, name)
    await secrets.clear_secret(project.pk, secrets.helper_key_name(name))
    await helper.adelete()
    await sync_to_async(log_action)(
        request, action="delete", resource_type="helper",
        resource_id=str(pid), metadata={"projectPid": str(pid), "name": name},
    )
    return 204, None


@router.post("/{pid}/helpers/{name}/test", response=dict)
async def test_helper(request, pid: uuid.UUID, name: str, data: HelperTryIn):
    """One small call to the helper with its stored key; the status only."""
    project, _ = await _project_for(request, pid)
    helper = await _helper_or_404(project, name)
    if not data.model:
        raise HttpError(400, "name the model to call")
    key = await secrets.get_secret(project.pk, secrets.helper_key_name(name))
    # a helper is reached as an OpenAI-compatible endpoint; the adapter is the same one
    probe = Connection(
        project=project, name=f"helper:{helper.name}", description="",
        kind="openai", base_url=helper.endpoint_url, model=data.model, timeout_s=30,
    )
    outcome = await adapters.try_connection(probe, Secret(key=key), input="ping")
    return {
        "ok": bool(outcome["ok"]),
        "status": "reachable" if outcome["ok"] else outcome.get("error", "failed"),
        "detail": outcome.get("detail") or "",
        "latency_ms": outcome.get("latency_ms", 0),
    }
