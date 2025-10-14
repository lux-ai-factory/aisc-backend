import uuid
from pathlib import Path

from ninja import Router, File
from ninja.errors import HttpError
from ninja.files import UploadedFile

from a4s_backend.models.dataset import Dataset
from a4s_backend.models.datashape import DataShapeStatus
from a4s_backend.repositories.file_repository import upload_file
from a4s_backend.schemas.common import UploadFileResponse
from a4s_backend.schemas.dataset import DatasetOutScheme
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

    result = upload_file(file, S3_DATASETS_BUCKET)

    if not result:
        raise HttpError(500, "Failed to upload file")

    # Call evaluation engine to autodiscover datashape
    autodiscover_datashape(dataset.datashape.pid)

    dataset.datashape.status = DataShapeStatus.Requested
    dataset.data = file.name
    await dataset.asave()
    await dataset.datashape.asave()

    return UploadFileResponse(file_name=file.name)


@router.get("/{dataset_pid}/datashape", response=DatasetOutScheme)
async def get_dataset_datashape(dataset_pid: uuid.UUID):
    dataset: Dataset = await Dataset.objects.select_related("datashape").aget(pid=dataset_pid)
    if not dataset:
        raise HttpError(404, f"Dataset ({dataset_pid}) not found")

    return None