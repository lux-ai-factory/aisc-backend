import uuid

from ninja import Router
from ninja.errors import HttpError

from aisc_backend.models import ProjectConfig, ProjectConfigCategory
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project_config import (
    ProjectConfigInSchema,
    ProjectConfigOutSchema,
    ProjectConfigUpdateSchema,
)
from aisc_backend.services.project_config_keys import create_project_config_key
from aisc_backend.utils.encryption import encrypt_value

router = Router(tags=["project settings"])
project_config_repository = ProjectConfigRepository()
project_repository = ProjectRepository()


def _masked(value: str) -> str:
    return value[:4] + "..." + value[-4:] if len(value) > 8 else "..." + value[-4:]


@router.get("/{project_pid}", response=list[ProjectConfigOutSchema])
async def list_project_configs(request, project_pid: uuid.UUID):
    return await project_config_repository.get_by_project(project_pid)


@router.post("/{project_pid}", response=ProjectConfigOutSchema)
async def create_project_config(request, project_pid: uuid.UUID, data: ProjectConfigInSchema):
    project = await project_repository.get(project_pid)
    try:
        key = await create_project_config_key(project, data.category, data.key)
    except ValueError as error:
        raise HttpError(409, str(error))
    is_secret = data.category == ProjectConfigCategory.SECRETS
    project_config = await project_config_repository.create(
        ProjectConfig(
            project=project,
            category=data.category,
            key=key,
            name=data.name,
            encrypted_value=encrypt_value(data.value) if is_secret else "",
            masked_value=_masked(data.value) if is_secret else "",
            json_value=data.json_value.model_dump() if data.json_value is not None else {},
        )
    )
    return project_config


@router.get("/{project_pid}/available", response=dict[str, list[ProjectConfigOutSchema]])
async def available_project_config(request, project_pid: uuid.UUID):
    result: dict[str, list[ProjectConfigOutSchema]] = {category.value: [] for category in ProjectConfigCategory}
    for project_config in await project_config_repository.get_by_project(project_pid):
        result[project_config.category].append(project_config)
    return result


@router.patch("/{project_pid}/{project_config_pid}", response=ProjectConfigOutSchema)
async def update_project_config(request, project_pid: uuid.UUID, project_config_pid: uuid.UUID, data: ProjectConfigUpdateSchema):
    project_config = await project_config_repository.get(project_config_pid)
    if project_config.project != project_pid:
        raise HttpError(400, f"Project config not associated with project id")
    if data.name is not None:
        project_config.name = data.name
    if data.value is not None:
        if project_config.category != ProjectConfigCategory.SECRETS:
            raise HttpError(400, "value is only valid for secret")
        project_config.encrypted_value = encrypt_value(data.value)
        project_config.masked_value = _masked(data.value)
    if data.json_value is not None:
        project_config.json_value = data.json_value.model_dump()
    await project_config_repository.save(project_config)
    return project_config


@router.delete("/{project_pid}/{project_config_pid}", response={204: None})
async def delete_project_config(request, project_pid: uuid.UUID, project_config_pid: uuid.UUID):
    project_config = await project_config_repository.get(project_config_pid)
    await project_config_repository.delete(project_config)
    return 204, None
