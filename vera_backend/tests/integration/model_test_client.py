import uuid
from unittest.mock import patch, MagicMock

from django.http import StreamingHttpResponse
from ninja.testing import TestAsyncClient

from vera_backend.routers.model import router, UploadModelFileResponse

from django.core.files.uploadedfile import SimpleUploadedFile

client = TestAsyncClient(router)


# Mock file_repository calls
async def upload_model_file(pid: uuid.UUID) -> UploadModelFileResponse:
    with (
        patch("vera_backend.routers.model.file_repository.upload_file", new_callable=MagicMock) as mock_upload_response,
    ):
        mock_upload_response.return_value = True

        file = SimpleUploadedFile(
            "model.onnx",
            b"ONNX",
            content_type="application/octet-stream",
        )

        response = await client.put(
            f'/{pid}/data',
            FILES={"file": file},
        )
        upload_model_file_response = UploadModelFileResponse.model_construct(**response.data)
        return upload_model_file_response


# Mock file_repository calls
async def get_model_file_data(pid: uuid.UUID) -> StreamingHttpResponse:
    with (
        patch("vera_backend.routers.dataset.file_repository.bucket_exists", new_callable=MagicMock) as mock_bucket_exists_response,
        patch("vera_backend.routers.dataset.file_repository.get_object", new_callable=MagicMock) as mock_get_object_response,
    ):
        mock_bucket_exists_response.return_value = True
        mock_get_object_response.return_value = {'Body': b"ONNX"}

        response = await client.get(f'/{pid}/data')
        return response