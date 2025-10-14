import uuid
from pathlib import Path

from ninja import Router, File
from ninja.errors import HttpError
from ninja.files import UploadedFile

from a4s_backend.models.model import Model
from a4s_backend.repositories.file_repository import upload_file
from a4s_backend.schemas.common import UploadFileResponse
from a4s_backend.utils import file_utils

from config.settings import S3_MODELS_BUCKET


router = Router(tags=["model"])


@router.put("/{model_pid}/data", response=UploadFileResponse)
async def upload_model(request, model_pid: uuid.UUID, file: File[UploadedFile]):
    if not file or not file.name:
        raise HttpError(500, "Invalid file")

    model: Model = await Model.objects.aget(pid=model_pid)
    if not model:
        raise HttpError(404, f"Model ({model_pid}) not found")

    # Check if file is CSV and convert to parquet if needed
    if Path(file.name).suffix.lower() == ".csv":
        file_utils.csv_to_parquet(file)

    suffix = Path(file.name).suffix.lower()
    if suffix != ".onnx":
        raise HttpError(400, f"File ({file.name}) is not an ONNX file")

    file.name = f"{str(uuid.uuid4())}{suffix}"

    result = upload_file(file, S3_MODELS_BUCKET)

    if not result:
        raise HttpError(500, "Failed to upload file")

    model.data = file.name
    await model.asave()

    return UploadFileResponse(file_name=file.name)