import uuid
from pathlib import Path

from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse
from ninja import Router, File, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from aisc_backend.audit.log import log_action
from aisc_backend.models import AIComponent, AIComponentType
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.ai_component_repository import AIComponentRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.schemas.ai_system import AIComponentInSchema, AIComponentOutSchema
from aisc_backend.services import component_secrets as secrets
from aisc_backend.services import components as component_service
from aisc_backend.utils.encryption import decrypt_value
from aisc_plugin_interface import list_openai_models, ModelListingError
from config.settings import MODEL_LISTING_SSL_VERIFY

router = Router(tags=["component"])

ai_component_repository = AIComponentRepository()
project_config_repository = ProjectConfigRepository()


class UploadComponentFileResponse(Schema):
    file_name: str
    file_size: int


class ComponentTryIn(Schema):
    input: str | None = None
    history: list[dict] | None = None
    params: dict | None = None


class ComponentTryOut(Schema):
    ok: bool
    answer: object | None = None
    refused: bool = False
    refusal_reason: str | None = None
    error: str | None = None
    detail: str = ""
    capabilities: dict[str, bool] = {}
    latency_ms: int = 0


def storage_container_for(component: AIComponent) -> str:
    if component.component_type == AIComponentType.DATASET:
        return StorageContainer.Datasets
    return StorageContainer.Models


@router.get("/{component_pid}", response=AIComponentOutSchema)
async def get_component(request, component_pid: uuid.UUID):
    component = await ai_component_repository.get_with_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    return await component_service.component_row(component)


@router.patch("/{component_pid}", response=AIComponentOutSchema)
async def update_component(request, component_pid: uuid.UUID, data: AIComponentInSchema):
    component = await ai_component_repository.get_with_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")

    project_pid = str(component.project.pid)

    if data.name is not None:
        component.name = data.name
    if data.description is not None:
        component.description = data.description
    if data.json_value is not None:
        component.json_value = data.json_value
    if data.source_dataset_pid:
        source = await ai_component_repository.get(data.source_dataset_pid)
        if source is None or source.component_type != AIComponentType.DATASET:
            raise HttpError(400, "source_dataset must be a dataset component")
        component.source_dataset = source

    if data.preset:
        try:
            component_service.apply_preset(component, data.preset, data.fields or {})
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
    if data.component_type is not None:
        component.component_type = data.component_type
    if data.protocol is not None:
        component.protocol = data.protocol
    if data.url is not None:
        component.url = data.url
    if data.adapter is not None:
        component.adapter = data.adapter
    if data.adapter_version is not None:
        component.adapter_version = data.adapter_version
    if data.adapter_config is not None:
        try:
            component.adapter_config = component_service.validate_config(
                component.adapter, data.adapter_config
            )
        except Exception as exc:
            raise HttpError(400, f"adapter config is invalid: {exc}") from exc

    component = await ai_component_repository.save(component)
    if data.secret is not None:
        key_name = component.secret_key or secrets.component_key_name(component.pid)
        if data.secret == "":
            await secrets.clear_secret(component.project_id, key_name)
        else:
            await secrets.set_secret(component.project_id, key_name, data.secret)
            if component.secret_key != key_name:
                component.secret_key = key_name
                await component.asave(update_fields=["secret_key"])
    for header, value in (data.secret_headers or {}).items():
        if value == "":
            await secrets.remove_secret_header(component, header)
        else:
            await secrets.set_secret_header(component, header, value)

    await sync_to_async(log_action)(
        request, action="update", resource_type="component",
        resource_id=str(component_pid),
        metadata={"projectPid": project_pid, "name": component.name})
    return await component_service.component_row(component)


@router.delete("/{component_pid}", response={204: None})
async def delete_component(request, component_pid: uuid.UUID):
    component = await ai_component_repository.get_with_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    project_pid = str(component.project.pid)
    # every secret it stored goes with it
    for header in await secrets.secret_header_names(component):
        await secrets.remove_secret_header(component, header)
    if component.secret_key:
        await secrets.clear_secret(component.project_id, component.secret_key)
    await ai_component_repository.delete(component)
    await sync_to_async(log_action)(
        request, action="delete", resource_type="component",
        resource_id=str(component_pid),
        metadata={"projectPid": project_pid, "name": component.name,
                  "component_type": component.component_type})
    return 204, None


@router.put("/{component_pid}/data", response=UploadComponentFileResponse)
async def upload_component_file(request, component_pid: uuid.UUID, file: File[UploadedFile]):
    if not file or not file.name:
        raise HttpError(500, "Invalid file")

    component = await ai_component_repository.get_with_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if not component.is_file_backed:
        raise HttpError(400, "Only dataset/model/file components accept an uploaded file")

    project_pid = str(component.project.pid)

    suffix = Path(file.name).suffix.lower()
    file.name = f"{str(uuid.uuid4())}{suffix}"

    container = storage_container_for(component)
    result = file_repository.upload_file(file, container)

    if not result:
        raise HttpError(500, "Failed to upload file")

    component.data = file.name
    component.file_size = file.size
    component.storage_container = container
    component.format = suffix.removeprefix(".")
    await ai_component_repository.save(component)

    await sync_to_async(log_action)(
        request, action="upload", resource_type="component",
        resource_id=str(component_pid),
        metadata={"projectPid": project_pid, "filename": file.name, "filesize": file.size,
                  "component_type": component.component_type})
    return UploadComponentFileResponse(file_name=file.name, file_size=file.size)


@router.get("/{component_pid}/models", response=dict)
async def get_component_models(request, component_pid: uuid.UUID):
    """List the models exposed by an OpenAI-compatible endpoint component.

    The API key is decrypted server-side and never sent to the client. On
    failure, returns an empty list plus an error message so the UI can fall
    back to a free-text model input.
    """
    component = await ai_component_repository.get_with_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if not component.is_endpoint or component.protocol != "openai-chat":
        raise HttpError(400, "Model listing is only available for OpenAI-compatible components")
    endpoint_url = component.url
    secret_key = component.secret_key

    if not endpoint_url:
        raise HttpError(400, "The component has no endpoint URL configured")
    if not secret_key:
        raise HttpError(400, "The component has no API key secret configured")

    secret = await project_config_repository.get_secret_by_key(component.project.pid, secret_key)
    if secret is None or not secret.encrypted_value:
        raise HttpError(400, "Configured API key secret not found")

    try:
        api_key = decrypt_value(secret.encrypted_value)
        models = list_openai_models(
            endpoint_url,
            api_key,
            verify_ssl=MODEL_LISTING_SSL_VERIFY,
        )
        return {"models": models, "error": None}
    except ModelListingError as exc:
        return {"models": [], "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"models": [], "error": str(exc)}


@router.post("/{component_pid}/test", response=ComponentTryOut)
async def test_component(
    request, component_pid: uuid.UUID, data: ComponentTryIn | None = None
):
    """One call through the component's adapter, as a test would make it (the
    pilot's "Try"). The allowlist is read immediately before the call out, so a
    change to it applies to the next call with no restart."""
    component = await ai_component_repository.get_with_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if not component.is_endpoint:
        raise HttpError(400, "only an endpoint component can be tested")

    from aisc_backend.services import allowed_hosts as hosts

    refusal = await hosts.guard_url(component.project, component.url)
    if refusal is not None:
        return refusal

    return await component_service.try_component(
        component,
        input=(data.input if data else "ping"),
        history=(data.history if data else None),
        params=(data.params if data else None),
    )


@router.get("/{component_pid}/data")
async def get_component_file(request, component_pid: uuid.UUID):
    try:
        component = await ai_component_repository.get(component_pid)
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
