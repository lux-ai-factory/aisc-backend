import uuid
from pathlib import Path

from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse
from ninja import Router, File, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from aisc_backend.audit.log import log_action
from aisc_backend.models import AIComponent, AIComponentType, ProjectConfig, ProjectConfigCategory
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository
from aisc_backend.schemas.ai_system import AIComponentInSchema, AIComponentOutSchema

router = Router(tags=["component"])

component_repository = BaseRepository(model=AIComponent)


class UploadComponentFileResponse(Schema):
    file_name: str
    file_size: int


def storage_container_for(component: AIComponent) -> str:
    if component.component_type == AIComponentType.DATASET:
        return StorageContainer.Datasets
    return StorageContainer.Models


@router.get("/{component_pid}", response=AIComponentOutSchema)
async def get_component(request, component_pid: uuid.UUID):
    component = await (
        AIComponent.objects.select_related("secret", "source_dataset")
        .filter(pid=component_pid)
        .afirst()
    )
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    return component


@router.patch("/{component_pid}", response=AIComponentOutSchema)
async def update_component(request, component_pid: uuid.UUID, data: AIComponentInSchema):
    component = await component_repository.get(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")

    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        component.name = values["name"]
    if "description" in values:
        component.description = values["description"]
    if "endpoint_url" in values:
        component.endpoint_url = values["endpoint_url"]
    if "json_value" in values:
        component.json_value = values["json_value"]
    if values.get("secret_pid"):
        secret = await ProjectConfig.objects.filter(
            pid=values["secret_pid"], category=ProjectConfigCategory.SECRETS
        ).afirst()
        if secret is None:
            raise HttpError(400, "Not found: secret does not exist")
        component.secret = secret
    if values.get("source_dataset_pid"):
        source = await component_repository.get(values["source_dataset_pid"])
        if source is None or source.component_type != AIComponentType.DATASET:
            raise HttpError(400, "source_dataset must be a dataset component")
        component.source_dataset = source

    component = await component_repository.save(component)
    component = await (
        AIComponent.objects.select_related("secret", "source_dataset")
        .filter(pid=component.pid).afirst()
    )
    await sync_to_async(log_action)(
        request, action="update", resource_type="component",
        resource_id=str(component_pid), metadata={"name": component.name})
    return component


@router.delete("/{component_pid}", response={204: None})
async def delete_component(request, component_pid: uuid.UUID):
    component = await component_repository.get(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    await component_repository.delete(component)
    return 204, None


@router.put("/{component_pid}/data", response=UploadComponentFileResponse)
async def upload_component_file(request, component_pid: uuid.UUID, file: File[UploadedFile]):
    if not file or not file.name:
        raise HttpError(500, "Invalid file")

    component = await component_repository.get(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if not component.is_file_backed:
        raise HttpError(400, "Only dataset/model/file components accept an uploaded file")

    suffix = Path(file.name).suffix.lower()
    file.name = f"{str(uuid.uuid4())}{suffix}"

    container = storage_container_for(component)
    result = file_repository.upload_file(file, container)

    if not result:
        raise HttpError(500, "Failed to upload file")

    component.data = file.name
    component.file_size = file.size
    component.storage_container = container
    await component_repository.save(component)

    await sync_to_async(log_action)(
        request, action="upload", resource_type="component",
        resource_id=str(component_pid),
        metadata={"filename": file.name, "filesize": file.size,
                  "component_type": component.component_type})
    return UploadComponentFileResponse(file_name=file.name, file_size=file.size)


@router.get("/{component_pid}/data")
async def get_component_file(request, component_pid: uuid.UUID):
    try:
        component = await component_repository.get(component_pid)
        if not component:
            raise HttpError(404, f"Component {component_pid} not found")

        container = component.storage_container or storage_container_for(component)

        bucket_exists = file_repository.bucket_exists(container)
        if not bucket_exists:
            raise HttpError(500, f"Bucket {container} not found")

        response = file_repository.get_object(bucket_name=container,
                                              object_name=component.data)
        file_stream = response["Body"]

        content_type = response.get("ContentType", "application/octet-stream")

        return StreamingHttpResponse(
            file_stream,
            headers={"Content-Disposition": f"attachment; filename={component.data}"},
            content_type=content_type,
        )
    except Exception as e:
        raise HttpError(500, f"Error fetching component file: {str(e)}")
