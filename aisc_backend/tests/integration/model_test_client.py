import uuid
from unittest.mock import patch, MagicMock

from django.http import StreamingHttpResponse
from ninja.testing import TestAsyncClient

from aisc_backend.routers.component import router, UploadComponentFileResponse

from django.core.files.uploadedfile import SimpleUploadedFile

client = TestAsyncClient(router)


async def upload_model_file(pid: uuid.UUID) -> UploadComponentFileResponse:
    with (
        patch("aisc_backend.routers.component.file_repository.upload_file", new_callable=MagicMock) as mock_upload_response,
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
        upload_model_file_response = UploadComponentFileResponse.model_construct(**response.data)
        return upload_model_file_response


async def get_model_file_data(pid: uuid.UUID) -> StreamingHttpResponse:
    with (
        patch("aisc_backend.routers.component.file_repository.bucket_exists", new_callable=MagicMock) as mock_bucket_exists_response,
        patch("aisc_backend.routers.component.file_repository.get_object", new_callable=MagicMock) as mock_get_object_response,
    ):
        mock_bucket_exists_response.return_value = True
        mock_get_object_response.return_value = {'Body': b"ONNX"}

        response = await client.get(f'/{pid}/data')
        return response
