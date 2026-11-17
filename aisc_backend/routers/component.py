import hashlib
import os
import uuid
from pathlib import Path

from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse
from django.utils import timezone
from ninja import Router, File, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from aisc_backend import component_endpoints
from aisc_backend.audit.log import log_action
from aisc_backend.models import AIComponent, AIComponentType, EvaluationInput
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.ai_component_repository import AIComponentRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.schemas.ai_system import AIComponentOutSchema, AIComponentUpdateSchema
from aisc_backend.routers.project_config import _masked as masked_secret_value
from aisc_backend.utils.encryption import decrypt_value, encrypt_value
from aisc_plugin_interface import LLMConfig, ModelListingError
from config.settings import MODEL_LISTING_SSL_VERIFY

router = Router(tags=["component"])

ai_component_repository = AIComponentRepository()
project_config_repository = ProjectConfigRepository()


REFERENCE_PREFIXES = ("target:", "connection:")


def check_reference_of_project(json_value, project) -> None:
    """Refuse (400) a component value naming a target or connection that is not of `project`.

    Only a `json_value["value"]` string starting with `target:` or `connection:` is checked: it must be
    well formed and name the platform pid `project` is linked to. Any other value passes.
    """
    from aisc_plugin_interface.connections import BadReference, parse_reference
    from aisc_plugin_interface.targets import parse_target

    value = json_value.get("value") if isinstance(json_value, dict) else None
    if not isinstance(value, str) or not value.startswith(REFERENCE_PREFIXES):
        return
    try:
        pid, _ = parse_target(value) if value.startswith("target:") else parse_reference(value)
        named = uuid.UUID(pid)
    except (BadReference, ValueError, TypeError, AttributeError):
        raise HttpError(400, f"{value} is not a well-formed target or connection reference") from None
    if project.platform_project_id is None:
        raise HttpError(400, "this project is linked to no platform project, so a component cannot name a "
                             "target or connection")
    if named != uuid.UUID(str(project.platform_project_id)):
        raise HttpError(400, f"{value} names another project's target or connection: a component may name "
                             "only its own project's")


class UploadComponentFileResponse(Schema):
    file_name: str
    file_size: int


def storage_container_for(component: AIComponent) -> str:
    if component.component_type == AIComponentType.DATASET:
        return StorageContainer.Datasets
    return StorageContainer.Models


@router.get("/{component_pid}", response=AIComponentOutSchema)
async def get_component(request, component_pid: uuid.UUID):
    component = await ai_component_repository.get_with_source_dataset(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    return component


@router.patch("/{component_pid}", response=AIComponentOutSchema)
async def update_component(request, component_pid: uuid.UUID, data: AIComponentUpdateSchema):
    """Edit a component. An LLM component that keeps its secret while its endpoint moves to another origin needs
    the secret typed again (secret_value, which becomes that project secret's value): otherwise 422."""
    component = await ai_component_repository.get_with_system_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if data.json_value is not None:
        check_reference_of_project(data.json_value, component.system.project)
    retyped = None
    if (component.component_type == AIComponentType.LLM and data.json_value is not None
            and component_endpoints.must_type_the_key_again(component.json_value, data.json_value)):
        if not (data.secret_value or "").strip():
            raise HttpError(422, component_endpoints.ENTER_THE_KEY_AGAIN)
        retyped = await project_config_repository.get_secret_by_key(
            component.system.project.pid, data.json_value["secret_key"])
        if retyped is None:
            raise HttpError(400, "Configured API key secret not found")
        retyped.encrypted_value = encrypt_value(data.secret_value.strip())
        retyped.masked_value = masked_secret_value(data.secret_value.strip())

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

    if retyped is not None:
        await project_config_repository.save(retyped)
    component = await ai_component_repository.save(component)
    component = await ai_component_repository.get_with_source_dataset(component.pid)
    await sync_to_async(log_action)(
        request, action="update", resource_type="component",
        resource_id=str(component_pid), metadata={"name": component.name})
    return component


class RetiredOutSchema(Schema):
    retired: bool
    evaluations: int


@router.delete("/{component_pid}", response={200: RetiredOutSchema, 204: None})
async def delete_component(request, component_pid: uuid.UUID):
    """Delete the component; one that evaluations used is retired instead, so their record keeps what they ran
    on (an evaluation's inputs protect their component)."""
    component = await ai_component_repository.get(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    used_by = await (EvaluationInput.objects.filter(component=component)
                     .values("evaluation_plugin__evaluation").distinct().acount())
    if used_by:
        if component.retired_at is None:
            component.retired_at = timezone.now()
            await component.asave(update_fields=["retired_at"])
            await sync_to_async(log_action)(
                request, action="retire", resource_type="component", resource_id=str(component_pid),
                metadata={"name": component.name, "evaluations": used_by})
        return 200, {"retired": True, "evaluations": used_by}
    await ai_component_repository.delete(component)
    if component.data and component.storage_container:
        # once the row is gone nothing names the file: it goes too (a refusal is logged, the delete stands)
        await sync_to_async(file_repository.remove_object)(component.storage_container, component.data)
    await sync_to_async(log_action)(
        request, action="delete", resource_type="component", resource_id=str(component_pid),
        metadata={"name": component.name, "component_type": component.component_type})
    return 204, None


@router.post("/{component_pid}/restore", response=AIComponentOutSchema)
async def restore_component(request, component_pid: uuid.UUID):
    """Bring a retired component back into the lists and the choices for new runs."""
    component = await ai_component_repository.get(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if component.retired_at is not None:
        component.retired_at = None
        await component.asave(update_fields=["retired_at"])
        await sync_to_async(log_action)(
            request, action="restore", resource_type="component", resource_id=str(component_pid),
            metadata={"name": component.name})
    return await ai_component_repository.get_with_source_dataset(component.pid)


#: the largest component file, in MB, when TARGET_FILE_MAX_MB does not say
UPLOAD_CAP_MB = 2048

#: the file extensions each file-backed component type takes
EXTENSIONS: dict[str, frozenset[str]] = {
    AIComponentType.DATASET: frozenset({".csv", ".tsv", ".parquet", ".json", ".jsonl", ".zip"}),
    AIComponentType.MODEL: frozenset({".onnx", ".pkl", ".joblib", ".pt", ".pth", ".safetensors", ".h5", ".keras",
                                      ".zip"}),
}


def upload_cap_bytes() -> int:
    """The largest file a component or a plugin artifact may be: TARGET_FILE_MAX_MB, read at each call, or
    UPLOAD_CAP_MB when it is unset or not a positive whole number."""
    try:
        mb = int(os.environ.get("TARGET_FILE_MAX_MB") or UPLOAD_CAP_MB)
    except ValueError:
        mb = UPLOAD_CAP_MB
    return (mb if mb > 0 else UPLOAD_CAP_MB) * 1024 * 1024


@router.put("/{component_pid}/data", response=UploadComponentFileResponse)
async def upload_component_file(request, component_pid: uuid.UUID, file: File[UploadedFile]):
    """Store the file of a dataset or model component. A file without a name or empty (422), above the cap
    (413) or without an extension of its component's type (415) is refused before anything is stored."""
    if not file or not file.name:
        raise HttpError(422, "the file has no name")

    component = await ai_component_repository.get(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if not component.is_file_backed:
        raise HttpError(400, "Only dataset/model/file components accept an uploaded file")
    if component.data and await EvaluationInput.objects.filter(component=component).aexists():
        # a past run names this component: replacing its file would point that run at bytes it never read
        raise HttpError(409, f"{component.name} was used by a run, so its file is kept as it is: add a new "
                             "component for the new file")

    if not file.size:
        raise HttpError(422, "the file is empty")
    cap = upload_cap_bytes()
    if file.size > cap:
        raise HttpError(413, f"a file is at most {cap // (1024 * 1024)} MB")
    suffix = Path(file.name).suffix.lower()
    allowed = EXTENSIONS.get(component.component_type, frozenset())
    if suffix not in allowed:
        raise HttpError(415, f"a {component.component_type} file ends in one of {', '.join(sorted(allowed))}")
    file.name = f"{str(uuid.uuid4())}{suffix}"
    digest = hashlib.sha256()
    for chunk in file.chunks():
        digest.update(chunk)
    file.seek(0)

    container = storage_container_for(component)
    result = file_repository.upload_file(file, container)

    if not result:
        raise HttpError(500, "Failed to upload file")

    component.data = file.name
    component.file_size = file.size
    component.storage_container = container
    component.data_sha256 = digest.hexdigest()
    await ai_component_repository.save(component)

    await sync_to_async(log_action)(
        request, action="upload", resource_type="component",
        resource_id=str(component_pid),
        metadata={"filename": file.name, "filesize": file.size,
                  "component_type": component.component_type})
    return UploadComponentFileResponse(file_name=file.name, file_size=file.size)


@router.get("/{component_pid}/models", response=dict)
async def get_component_models(request, component_pid: uuid.UUID):
    """List the models exposed by an llm component's OpenAI-compatible endpoint.

    The API key is decrypted server-side and never sent to the client. It goes only to an address the
    project's network rule allows, with no redirect followed (component_endpoints). On failure, returns an
    empty list plus an error message, never the endpoint's answer, so the UI can fall back to a free-text
    model input.
    """
    component = await ai_component_repository.get_with_system_project(component_pid)
    if not component:
        raise HttpError(404, f"Component {component_pid} not found")
    if component.component_type != AIComponentType.LLM:
        raise HttpError(400, "Model listing is only available for llm components")

    config = LLMConfig.model_validate(component.json_value or {})
    if not config.endpoint_url:
        raise HttpError(400, "LLM component has no endpoint_url configured")
    if not config.secret_key:
        raise HttpError(400, "LLM component has no API key secret configured")

    secret = await project_config_repository.get_secret_by_key(
        component.system.project.pid, config.secret_key
    )
    if secret is None or not secret.encrypted_value:
        raise HttpError(400, "Configured API key secret not found")

    try:
        rule = await sync_to_async(component_endpoints.network_rule)(component.system.project)
        api_key = decrypt_value(secret.encrypted_value)
        models = await sync_to_async(component_endpoints.list_models)(
            config.endpoint_url,
            api_key,
            rule,
            verify_ssl=MODEL_LISTING_SSL_VERIFY,
        )
        return {"models": models, "error": None}
    except ModelListingError as exc:
        return {"models": [], "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive: its kind only, never its text
        return {"models": [], "error": f"the models could not be listed ({type(exc).__name__})"}


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
