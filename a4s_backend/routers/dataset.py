import uuid
from pathlib import Path

from ninja import Router, File
from ninja.errors import HttpError
from ninja.files import UploadedFile

from django.http import StreamingHttpResponse

from a4s_backend.models.dataset import Dataset
from a4s_backend.models.datashape import DataShapeStatus, DataShape
from a4s_backend.repositories import file_repository
from a4s_backend.repositories.datashape_repository import save_datashape

from a4s_backend.schemas.common import UploadFileResponse
from a4s_backend.schemas.datashape import DataShapeOutScheme, DataShapeInScheme
from a4s_backend.services.a4s_eval import autodiscover_datashape

from a4s_backend.utils import file_utils

from config.settings import S3_DATASETS_BUCKET


router = Router(tags=["dataset"])


@router.put("/{dataset_pid}/data", response=UploadFileResponse)
async def upload_dataset(request, dataset_pid: uuid.UUID, file: File[UploadedFile]):
    if not file or not file.name:
        raise HttpError(500, "Invalid file")

    dataset: Dataset = await Dataset.objects.select_related("datashape").aget(pid=dataset_pid)
    if not dataset:
        raise HttpError(404, f"Dataset ({dataset_pid}) not found")

    # Check if file is CSV and convert to parquet if needed
    if Path(file.name).suffix.lower() == ".csv":
        file_utils.csv_to_parquet(file)

    suffix = Path(file.name).suffix.lower()
    file.name = f"{str(uuid.uuid4())}{suffix}"

    result = file_repository.upload_file(file, S3_DATASETS_BUCKET)

    if not result:
        raise HttpError(500, "Failed to upload file")

    datashape = dataset.get_datashape()
    if datashape is None:
        raise HttpError(404, f"Datashape for dataset ({dataset_pid}) not found")

    # Call evaluation engine to autodiscover datashape
    autodiscover_datashape_response = await autodiscover_datashape(datashape.pid)
    if not autodiscover_datashape_response:
        raise HttpError(500, f"Autodiscovery failed in a4s evaluation module")

    datashape.status = DataShapeStatus.Requested
    dataset.data = file.name
    await dataset.asave()
    await datashape.asave()

    return UploadFileResponse(file_name=file.name)

@router.get("/{dataset_pid}/data")
async def get_dataset_file(request, dataset_pid: uuid.UUID):
    try:
        # check bucket exists
        bucket_exists = file_repository.bucket_exists(S3_DATASETS_BUCKET)
        if not bucket_exists:
            raise HttpError(500, f"Bucket {S3_DATASETS_BUCKET} not found")

        # Get dataset exists
        dataset = await Dataset.objects.aget(pid=dataset_pid)
        if not dataset:
            raise HttpError(404, f"Dataset ({dataset_pid}) not found")

        # Get the object from S3
        response = file_repository.get_object(bucket_name=S3_DATASETS_BUCKET, object_name=dataset.data)
        file_stream = response["Body"]

        return StreamingHttpResponse(
            file_stream,
            content_type="application/vnd.apache.parquet",
        )
    except Exception as e:
        raise HttpError(500, f"Error fetching dataset file: {str(e)}")


@router.get("/{dataset_pid}/datashape", response=DataShapeOutScheme)
async def get_dataset_datashape(request, dataset_pid: uuid.UUID):
    dataset = await (
        Dataset.objects
        .select_related("datashape", "project")
        .prefetch_related("datashape__features")
        .aget(pid=dataset_pid)
    )

    if not dataset:
        raise HttpError(404, f"Dataset ({dataset_pid}) not found")

    datashape = dataset.get_datashape()
    project = dataset.project
    project.expected_datashape = datashape
    await project.asave()

    return datashape

@router.patch("/{dataset_pid}/datashape", response=DataShapeOutScheme)
async def update_project(request, dataset_pid: uuid.UUID, data: DataShapeInScheme):
    dataset = await (
        Dataset.objects
        .select_related("datashape")
        .prefetch_related("datashape__features")
        .aget(pid=dataset_pid)
    )

    if not dataset:
        raise HttpError(404, f"Dataset ({dataset_pid}) not found")

    datashape: DataShape = dataset.get_datashape()

    if datashape is None:
        raise HttpError(404, f"Datashape for dataset ({dataset_pid}) not found")

    datashape = await save_datashape(datashape, data)
    return datashape
