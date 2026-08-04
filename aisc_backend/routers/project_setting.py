import uuid
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.models import ProjectSetting, ProjectSettingCategory
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.project_setting_repository import ProjectSettingRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project_setting import (
    DeriveFeaturesSchema,
    ProjectSettingInSchema,
    ProjectSettingOutSchema,
    ProjectSettingUpdateSchema,
    ValidateDatashapeSchema,
)
from aisc_backend.services.datashape_validation import validate_dataframe_against_datashape
from aisc_backend.services.feature_derivation import derive_features
from aisc_backend.utils.encryption import encrypt_value
from aisc_backend.models import Dataset

router = Router(tags=["project settings"])
settings_repository = ProjectSettingRepository()
project_repository = ProjectRepository()


def setting_out(setting: ProjectSetting) -> dict[str, Any]:
    return {
        "pid": setting.pid,
        "category": setting.category,
        "key": setting.key,
        "name": setting.name,
        "service_type": setting.service_type,
        "masked_value": setting.masked_value if setting.category == ProjectSettingCategory.API_KEY else "",
        "json_value": setting.json_value if setting.category != ProjectSettingCategory.API_KEY else {},
        "created_at": setting.created_at,
        "updated_at": setting.updated_at,
    }


async def project_setting(project_pid: uuid.UUID, setting_pid: uuid.UUID) -> ProjectSetting:
    try:
        return await settings_repository.get(
            setting_pid,
            project_pid=project_pid,
        )
    except ProjectSetting.DoesNotExist:
        raise HttpError(404, "Setting not found")


@router.get("/{project_pid}", response=list[ProjectSettingOutSchema])
async def list_settings(request, project_pid: uuid.UUID):
    return [setting_out(setting) for setting in await settings_repository.get_by_project(project_pid)]


@router.post("/{project_pid}", response=ProjectSettingOutSchema)
async def create_setting(request, project_pid: uuid.UUID, data: ProjectSettingInSchema):
    project = await project_repository.get(project_pid)
    values = data.model_dump()
    if data.category == ProjectSettingCategory.API_KEY:
        if not data.value:
            raise HttpError(400, "API key value is required")
        values["encrypted_value"] = encrypt_value(data.value)
        values["json_value"] = {}
        values.pop("value", None)
        plaintext = data.value
        values["masked_value"] = plaintext[:4] + "..." + plaintext[-4:] if len(plaintext) > 8 else "..." + plaintext[-4:]
    else:
        values.pop("value", None)
    values.pop("category", None)
    setting = await settings_repository.create(ProjectSetting(project=project, category=data.category, **values))
    return setting_out(setting)


@router.get("/{project_pid}/available", response=dict[str, list[ProjectSettingOutSchema]])
async def available_settings(request, project_pid: uuid.UUID):
    result: dict[str, list[dict]] = {category.value: [] for category in ProjectSettingCategory}
    for setting in await settings_repository.get_by_project(project_pid):
        result[setting.category].append(setting_out(setting))
    return result


@router.post("/{project_pid}/derive-features", response=ProjectSettingOutSchema)
async def derive_setting(request, project_pid: uuid.UUID, data: DeriveFeaturesSchema):
    project = await project_repository.get(project_pid)
    dataset = await Dataset.objects.aget(pid=data.dataset_pid, project=project)
    suffix = Path(dataset.data).suffix.lower()
    fmt = suffix.removeprefix(".")
    if fmt not in ("csv", "parquet"):
        raise HttpError(400, "Only CSV and Parquet datasets can produce a datashape")
    response = await sync_to_async(file_repository.get_object)(StorageContainer.Datasets, dataset.data)
    if not response:
        raise HttpError(404, "Dataset file not found")
    document = derive_features(response["Body"].read(), fmt, str(dataset.pid))
    setting = await settings_repository.create(ProjectSetting(
        project=project,
        category=ProjectSettingCategory.DATASHAPE,
        key=data.key,
        name=data.name,
        json_value=document,
    ))
    return setting_out(setting)


@router.patch("/{project_pid}/{setting_pid}", response=ProjectSettingOutSchema)
async def update_setting(request, project_pid: uuid.UUID, setting_pid: uuid.UUID, data: ProjectSettingUpdateSchema):
    setting = await project_setting(project_pid, setting_pid)
    values = data.model_dump(exclude_unset=True)
    plaintext = values.pop("value", None)
    if plaintext is not None:
        if setting.category != ProjectSettingCategory.API_KEY:
            raise HttpError(400, "value is only valid for API key settings")
        setting.encrypted_value = encrypt_value(plaintext)
        setting.masked_value = plaintext[:4] + "..." + plaintext[-4:] if len(plaintext) > 8 else "..." + plaintext[-4:]
    for key, value in values.items():
        setattr(setting, key, value)
    await settings_repository.save(setting)
    return setting_out(setting)


@router.delete("/{project_pid}/{setting_pid}", response={204: None})
async def delete_setting(request, project_pid: uuid.UUID, setting_pid: uuid.UUID):
    await settings_repository.delete(await project_setting(project_pid, setting_pid))
    return 204, None


@router.post("/{project_pid}/{setting_pid}/validate", response=dict)
async def validate_setting(request, project_pid: uuid.UUID, setting_pid: uuid.UUID, data: ValidateDatashapeSchema):
    setting = await project_setting(project_pid, setting_pid)
    if setting.category != ProjectSettingCategory.DATASHAPE:
        raise HttpError(400, "Only datashapes can validate datasets")
    dataset = await Dataset.objects.aget(pid=data.dataset_pid, project_id=setting.project_id)
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
