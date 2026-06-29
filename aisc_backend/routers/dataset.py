import uuid
from pathlib import Path

from ninja import Router, File, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from django.http import StreamingHttpResponse

from asgiref.sync import sync_to_async

from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository
from aisc_backend.repositories.dataset_repository import DatasetRepository
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.audit.log import log_action

router = Router(tags=["dataset"])

dataset_repository = DatasetRepository()
project_repository = ProjectRepository()


class UploadDatasetFileResponse(Schema):
    file_name: str


@router.put("/{dataset_pid}/data", response=UploadDatasetFileResponse)
async def upload_dataset_file(request, dataset_pid: uuid.UUID, file: File[UploadedFile]):
    if not file or not file.name:
        raise HttpError(500, "Invalid file")

    dataset = await dataset_repository.get(dataset_pid, True)
    if not dataset:
        raise HttpError(404, f"Dataset {dataset_pid} not found")

    suffix = Path(file.name).suffix.lower()
    file.name = f"{str(uuid.uuid4())}{suffix}"

    result = file_repository.upload_file(file, dataset.storage_container)

    if not result:
        raise HttpError(500, "Failed to upload file")

    dataset.data = file.name
    await dataset_repository.save(dataset)

    await sync_to_async(log_action)(
        request, "dataset:upload", {"datasetPid": str(dataset_pid), "filename": file.name})
    return UploadDatasetFileResponse(file_name=file.name)


@router.get("/{dataset_pid}/data")
async def get_dataset_file(request, dataset_pid: uuid.UUID):
    try:
        # check bucket exists
        bucket_exists = file_repository.bucket_exists(StorageContainer.Datasets)
        if not bucket_exists:
            raise HttpError(500, f"Bucket {StorageContainer.Datasets} not found")

        # Get dataset exists
        dataset = await dataset_repository.get(dataset_pid)

        # Get the object from S3
        response = file_repository.get_object(bucket_name=StorageContainer.Datasets, object_name=dataset.data)
        file_stream = response["Body"]

        return StreamingHttpResponse(
            file_stream,
            content_type="application/octet-stream",
        )
    except Exception as e:
        raise HttpError(500, f"Error fetching dataset file: {str(e)}")

