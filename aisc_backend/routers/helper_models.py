"""The project's helper models, for the evaluation form (configurator only).

A plugin's `helper:<role>` input is filled with one of the project's helper models, which the platform
keeps under Manage > Helper models (never a component of the AI system). The form lists them here, by
reference: what the platform answers the engine (platform_helpers), without the names it keeps them
under. config/urls.py mounts this router in configurator mode only, so standalone answers 404. The door
has already admitted the caller as a member of the project (project_door).
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend import platform_helpers
from aisc_backend.repositories.project_repository import ProjectRepository

router = Router(tags=["helper models"])

project_repository = ProjectRepository()


class HelperModelOutSchema(Schema):
    ref: str
    label: str
    roles: list[str]
    endpoint_host: str


async def helpers_of(project, role: str | None) -> list[dict]:
    """The helper models of the engine project's platform project (HelpersUnavailable as the platform's)."""
    if project.platform_project_id is None:
        return []
    return await sync_to_async(platform_helpers.list_helpers, thread_sensitive=False)(
        str(project.platform_project_id), role)


class HelperModelListSchema(Schema):
    models: list[str]
    error: str | None = None


@router.get("/{project_pid}/helpers/models", response=HelperModelListSchema)
async def project_helper_model_list(request, project_pid: uuid.UUID, ref: str):
    """The models the test tool `ref` (helper:<platform project>/<name>) can run, for the form's Model list: as
    the platform lists them, or none and the reason (the form then asks for the model's name)."""
    project = await project_repository.get(project_pid)
    prefix = f"helper:{project.platform_project_id}/" if project.platform_project_id else None
    if prefix is None or not ref.startswith(prefix):
        raise HttpError(404, "no such test tool in this project")
    try:
        return await sync_to_async(platform_helpers.list_helper_models, thread_sensitive=False)(
            str(project.platform_project_id), ref[len(prefix):])
    except platform_helpers.HelpersUnavailable as exc:
        return {"models": [], "error": exc.message}


@router.get("/{project_pid}/helpers", response=list[HelperModelOutSchema])
async def project_helper_models(request, project_pid: uuid.UUID, role: str | None = None):
    project = await project_repository.get(project_pid)
    try:
        helpers = await helpers_of(project, role)
    except platform_helpers.HelpersUnavailable as exc:
        raise HttpError(503, exc.message)
    return [{"ref": h["ref"], "label": h["label"], "roles": h["roles"], "endpoint_host": h["endpoint_host"]}
            for h in helpers]
