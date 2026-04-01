import uuid
from pathlib import Path

from django.http import StreamingHttpResponse
from ninja import Router, File, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from a4s_backend.models.common import StorageContainer
from a4s_backend.models.model import Model
from a4s_backend.repositories import file_repository
from a4s_backend.repositories.base_repository import BaseRepository

router = Router(tags=["model"])

model_repository = BaseRepository(model=Model)


class UploadModelFileResponse(Schema):
    file_name: str

@router.put("/{model_pid}/data", response=UploadModelFileResponse)
async def upload_model_file(request, model_pid: uuid.UUID, file: File[UploadedFile]):
    if not file or not file.name:
        raise HttpError(500, "Invalid file")

    model = await model_repository.get(model_pid)
    if not model:
        raise HttpError(404, f"Model {model_pid} not found")

    suffix = Path(file.name).suffix.lower()
    file.name = f"{str(uuid.uuid4())}{suffix}"

    result = file_repository.upload_file(file, StorageContainer.Models)

    if not result:
        raise HttpError(500, "Failed to upload file")

    model.data = file.name
    await model_repository.save(model)

    return UploadModelFileResponse(file_name=file.name)


@router.get("/{model_pid}/data")
async def get_model_file(request, model_pid: uuid.UUID):
    try:
        # check bucket exists
        bucket_exists = file_repository.bucket_exists(StorageContainer.Models)
        if not bucket_exists:
            raise HttpError(500, f"Bucket {StorageContainer.Models} not found")

        model = await model_repository.get(model_pid)

        # Get the object from S3
        response = file_repository.get_object(bucket_name=StorageContainer.Models, object_name=model.data)
        file_stream = response["Body"]

        content_type = response.get("ContentType", "application/octet-stream")

        return StreamingHttpResponse(
            file_stream,
            headers={"Content-Disposition": f"attachment; filename={model.data}"},
            content_type=content_type,
        )
    except Exception as e:
        raise HttpError(500, f"Error fetching dataset file: {str(e)}")
