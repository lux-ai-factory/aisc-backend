import uuid
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.models import AIComponent, AIComponentType, ProjectConfig, ProjectConfigCategory, EndpointType
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project_config import (
    DeriveFeaturesSchema,
    ProjectConfigInSchema,
    ProjectConfigOutSchema,
    ProjectConfigUpdateSchema,
    ValidateDatashapeSchema,
)
from aisc_backend.services.datashape_validation import validate_dataframe_against_datashape
from aisc_backend.services.feature_derivation import derive_features
from aisc_backend.utils.encryption import encrypt_value
from aisc_backend.services.project_config_keys import create_setting_key

router = Router(tags=["project settings"])
settings_repository = ProjectConfigRepository()
project_repository = ProjectRepository()


def setting_out(setting: ProjectConfig) -> dict[str, Any]:
    is_secret = setting.category in (ProjectConfigCategory.SECRETS, ProjectConfigCategory.API_ENDPOINT)
    return {
        "pid": setting.pid,
        "category": setting.category,
        "key": setting.key,
        "name": setting.name,
        "masked_value": setting.masked_value if is_secret else "",
        "json_value": setting.json_value if setting.category != ProjectConfigCategory.SECRETS else {},
        "endpoint_type": setting.endpoint_type,
        "url": setting.url,
        "created_at": setting.created_at,
        "updated_at": setting.updated_at,
    }


async def project_config(project_pid: uuid.UUID, setting_pid: uuid.UUID) -> ProjectConfig:
    try:
        return await settings_repository.get(
            setting_pid,
            project_pid=project_pid,
        )
    except ProjectConfig.DoesNotExist:
        raise HttpError(404, "Setting not found")


@router.get("/{project_pid}", response=list[ProjectConfigOutSchema])
async def list_settings(request, project_pid: uuid.UUID):
    return [setting_out(setting) for setting in await settings_repository.get_by_project(project_pid)]


@router.post("/{project_pid}", response=ProjectConfigOutSchema)
async def create_setting(request, project_pid: uuid.UUID, data: ProjectConfigInSchema):
    project = await project_repository.get(project_pid)
    values = data.model_dump()
    values.pop("category", None)
    values.pop("key", None)
    try:
        key = await create_setting_key(project, data.category, data.key)
    except ValueError as error:
        raise HttpError(409, str(error))
    if data.category == ProjectConfigCategory.SECRETS:
        if not data.value:
            raise HttpError(400, "API key value is required")
        values["encrypted_value"] = encrypt_value(data.value)
        values.pop("value", None)
        plaintext = data.value
        values["masked_value"] = plaintext[:4] + "..." + plaintext[-4:] if len(plaintext) > 8 else "..." + plaintext[-4:]
    elif data.category == ProjectConfigCategory.API_ENDPOINT:
        if not data.value:
            raise HttpError(400, "API key value is required")
        if not data.url:
            raise HttpError(400, "Endpoint URL is required")
        values["encrypted_value"] = encrypt_value(data.value)
        values.pop("value", None)
        plaintext = data.value
        values["masked_value"] = plaintext[:4] + "..." + plaintext[-4:] if len(plaintext) > 8 else "..." + plaintext[-4:]
        values["endpoint_type"] = data.endpoint_type or EndpointType.REST
        values["url"] = data.url
    else:
        values.pop("value", None)
    setting = await settings_repository.create(ProjectConfig(project=project, category=data.category, key=key, **values))
    return setting_out(setting)


@router.get("/{project_pid}/available", response=dict[str, list[ProjectConfigOutSchema]])
async def available_settings(request, project_pid: uuid.UUID):
    result: dict[str, list[dict]] = {category.value: [] for category in ProjectConfigCategory}
    for setting in await settings_repository.get_by_project(project_pid):
        result[setting.category].append(setting_out(setting))
    return result


@router.post("/{project_pid}/derive-features", response=ProjectConfigOutSchema)
async def derive_setting(request, project_pid: uuid.UUID, data: DeriveFeaturesSchema):
    project = await project_repository.get(project_pid)
    dataset = await AIComponent.objects.aget(
        pid=data.dataset_pid, system__project=project,
        component_type=AIComponentType.DATASET,
    )
    suffix = Path(dataset.data).suffix.lower()
    fmt = suffix.removeprefix(".")
    if fmt not in ("csv", "parquet"):
        raise HttpError(400, "Only CSV and Parquet datasets can produce a datashape")
    response = await sync_to_async(file_repository.get_object)(StorageContainer.Datasets, dataset.data)
    if not response:
        raise HttpError(404, "Dataset file not found")
    document = derive_features(response["Body"].read(), fmt, str(dataset.pid))
    try:
        key = await create_setting_key(project, ProjectConfigCategory.DATASHAPE, data.name)
    except ValueError as error:
        raise HttpError(409, str(error))
    setting = await settings_repository.create(ProjectConfig(
        project=project,
        category=ProjectConfigCategory.DATASHAPE,
        key=key,
        name=data.name,
        json_value=document,
    ))
    return setting_out(setting)


@router.patch("/{project_pid}/{setting_pid}", response=ProjectConfigOutSchema)
async def update_setting(request, project_pid: uuid.UUID, setting_pid: uuid.UUID, data: ProjectConfigUpdateSchema):
    setting = await project_config(project_pid, setting_pid)
    values = data.model_dump(exclude_unset=True)
    values.pop("key", None)
    plaintext = values.pop("value", None)
    if plaintext is not None:
        if setting.category not in (ProjectConfigCategory.SECRETS, ProjectConfigCategory.API_ENDPOINT):
            raise HttpError(400, "value is only valid for API key / endpoint settings")
        setting.encrypted_value = encrypt_value(plaintext)
        setting.masked_value = plaintext[:4] + "..." + plaintext[-4:] if len(plaintext) > 8 else "..." + plaintext[-4:]
    for key, value in values.items():
        setattr(setting, key, value)
    await settings_repository.save(setting)
    return setting_out(setting)


@router.delete("/{project_pid}/{setting_pid}", response={204: None})
async def delete_setting(request, project_pid: uuid.UUID, setting_pid: uuid.UUID):
    await settings_repository.delete(await project_config(project_pid, setting_pid))
    return 204, None


@router.post("/{project_pid}/{setting_pid}/validate", response=dict)
async def validate_setting(request, project_pid: uuid.UUID, setting_pid: uuid.UUID, data: ValidateDatashapeSchema):
    setting = await project_config(project_pid, setting_pid)
    if setting.category != ProjectConfigCategory.DATASHAPE:
        raise HttpError(400, "Only datashapes can validate datasets")
    dataset = await AIComponent.objects.aget(
        pid=data.dataset_pid, system__project_id=setting.project_id,
        component_type=AIComponentType.DATASET,
    )
    suffix = Path(dataset.data).suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise HttpError(400, "Only CSV and Parquet datasets can be validated")
    response = await sync_to_async(file_repository.get_object)(StorageContainer.Datasets, dataset.data)
    if not response:
        raise HttpError(404, "Dataset file not found")
    import io
    import pandas as pd
    content = response["Body"].read()
    frame = pd.read_csv(io.BytesIO(content)) if suffix == ".csv" else pd.read_parquet(io.BytesIO(content))
    return validate_dataframe_against_datashape(frame, setting.json_value)
