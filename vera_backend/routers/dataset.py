import uuid
from pathlib import Path

from ninja import Router, File, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from django.http import StreamingHttpResponse

from vera_backend.models.common import StorageContainer
from vera_backend.models.dataset import Dataset
from vera_backend.models.datashape import DataShape
from vera_backend.repositories import file_repository
from vera_backend.repositories.dataset_repository import DatasetRepository
from vera_backend.repositories.datashape_repository import DataShapeRepository
from vera_backend.repositories.project_repository import ProjectRepository
from vera_backend.schemas.datashape import DataShapeOutSchema, DataShapeInSchema

router = Router(tags=["dataset"])

dataset_repository = DatasetRepository()
datashape_repository = DataShapeRepository()
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


@router.get("/{dataset_pid}/datashape", response=DataShapeOutSchema)
async def get_dataset_datashape(request, dataset_pid: uuid.UUID):
    dataset = await dataset_repository.get(dataset_pid, True)

    datashape = dataset.get_datashape()
    project = dataset.project
    project.expected_datashape = datashape

    await project_repository.save(project)

    return datashape


@router.patch("/{dataset_pid}/datashape", response=DataShapeOutSchema)
async def update_dataset_datashape(request, dataset_pid: uuid.UUID, data: DataShapeInSchema):
    dataset: Dataset = await dataset_repository.get(dataset_pid, True)

    datashape: DataShape = dataset.get_datashape()

    if datashape is None:
        raise HttpError(404, f"Datashape for dataset ({dataset_pid}) not found")

    return await datashape_repository.patch(datashape, data)
