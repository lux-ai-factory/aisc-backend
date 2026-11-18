from django.http import StreamingHttpResponse
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.auth.access import role_or_404
from aisc_backend.models.ai_system import AIComponent, AIComponentType
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository


router = Router(tags=["file"])


async def _authorize_component_file(request, file_name: str, component_type: str) -> None:
    """A stored dataset/model file belongs to the project of the component whose
    `data` is this file name; only that project's members may read it."""
    component = (
        await AIComponent.objects.filter(data=file_name, component_type=component_type)
        .select_related("system__project")
        .afirst()
    )
    if component is None:
        raise HttpError(404, f"File {file_name} not found")
    await role_or_404(request, component.system.project)


@router.get("/dataset/{file_name}")
async def get_dataset_file(request, file_name: str):
    await _authorize_component_file(request, file_name, AIComponentType.DATASET)
    return await file_repository.get_s3_file_stream(StorageContainer.Datasets, file_name)


@router.get("/model/{file_name}")
async def get_model_file(request, file_name: str):
    await _authorize_component_file(request, file_name, AIComponentType.MODEL)
    return await file_repository.get_s3_file_stream(StorageContainer.Models, file_name)


@router.get("/artifact/{file_name}")
async def download_evaluation_artifact(request, file_name: str):
    artifact = (
        await Artifact.objects.filter(data=file_name)
        .select_related("evaluation_plugin__evaluation__project")
        .afirst()
    )
    if artifact is None:
        raise HttpError(404, f"Artifact with file name {file_name} not found")
    await role_or_404(request, artifact.evaluation_plugin.evaluation.project)

    streaming_response = await file_repository.get_s3_file_stream(StorageContainer.Artifacts, file_name)
    streaming_response["Content-Disposition"] = f'attachment; filename="{artifact.name}"'
    return streaming_response