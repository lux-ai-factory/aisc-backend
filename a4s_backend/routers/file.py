from django.http import StreamingHttpResponse
from ninja import Router
from ninja.errors import HttpError

from a4s_backend.models.artifact import Artifact
from a4s_backend.models.common import StorageContainer
from a4s_backend.repositories import file_repository
from a4s_backend.repositories.base_repository import BaseRepository


router = Router(tags=["file"])

artifact_repository = BaseRepository(model=Artifact)

async def _get_s3_file_stream(bucket_name: str, file_name: str) -> StreamingHttpResponse:
    try:
        if not file_repository.bucket_exists(bucket_name):
            raise HttpError(500, f"Bucket {bucket_name} not found")

        response = file_repository.get_object(bucket_name=bucket_name, object_name=file_name)
        return StreamingHttpResponse(response["Body"])
    except HttpError:
        raise
    except Exception as e:
        raise HttpError(500, f"Error fetching file from {bucket_name}: {str(e)}")


@router.get("/dataset/{file_name}")
async def get_dataset_file(request, file_name: str):
    return await _get_s3_file_stream(StorageContainer.Datasets, file_name)


@router.get("/model/{file_name}")
async def get_model_file(request, file_name: str):
    return await _get_s3_file_stream(StorageContainer.Models, file_name)


@router.get("/artifact/{file_name}")
async def download_evaluation_artifact(request, file_name: str):
    artifact: Artifact = await artifact_repository.get_one(data=file_name)
    if not artifact:
        raise HttpError(404, f"Artifact with file name {file_name} not found")

    streaming_response = await _get_s3_file_stream(StorageContainer.Artifacts, file_name)
    streaming_response["Content-Disposition"] = f'attachment; filename="{artifact.name}"'
    return streaming_response