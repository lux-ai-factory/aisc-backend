from django.http import StreamingHttpResponse
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.models.artifact import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.base_repository import BaseRepository


router = Router(tags=["file"])

artifact_repository = BaseRepository(model=Artifact)


@router.get("/dataset/{file_name}")
async def get_dataset_file(request, file_name: str):
    return await file_repository.get_s3_file_stream(StorageContainer.Datasets, file_name)


@router.get("/model/{file_name}")
async def get_model_file(request, file_name: str):
    return await file_repository.get_s3_file_stream(StorageContainer.Models, file_name)


@router.get("/artifact/{file_name}")
async def download_evaluation_artifact(request, file_name: str):
    artifact: Artifact = await artifact_repository.get_one(data=file_name)
    if not artifact:
        raise HttpError(404, f"Artifact with file name {file_name} not found")

    streaming_response = await file_repository.get_s3_file_stream(StorageContainer.Artifacts, file_name)
    streaming_response["Content-Disposition"] = f'attachment; filename="{artifact.name}"'
    return streaming_response