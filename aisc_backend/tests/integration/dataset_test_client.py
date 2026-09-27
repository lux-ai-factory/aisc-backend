import uuid
from unittest.mock import patch, MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import StreamingHttpResponse

from aisc_backend.routers.component import router, UploadComponentFileResponse

from ninja.testing import TestAsyncClient

from aisc_backend.utils.file_utils import csv_bytes_to_parquet_bytes

client = TestAsyncClient(router)

csv_file_content = b"feature1,feature2,target\n1,2,0\n3,4,1\n"
parquet_file_content = csv_bytes_to_parquet_bytes(csv_file_content)


async def upload_dataset_file(pid: uuid.UUID) -> UploadComponentFileResponse:
    with (
        patch("aisc_backend.routers.component.file_repository.upload_file",
              new_callable=MagicMock) as mock_upload_response,
    ):
        mock_upload_response.return_value = True

        file = SimpleUploadedFile(
            "test_dataset.csv",
            csv_file_content,
            content_type="text/csv",
        )

        response = await client.put(
            f'/{pid}/data',
            FILES={"file": file},
        )
        upload_dataset_file_response = UploadComponentFileResponse.model_construct(**response.data)
        return upload_dataset_file_response


async def get_dataset_file_data(pid: uuid.UUID) -> StreamingHttpResponse:
    with (
        patch("aisc_backend.routers.component.file_repository.bucket_exists", new_callable=MagicMock) as mock_bucket_exists_response,
        patch("aisc_backend.routers.component.file_repository.get_object", new_callable=MagicMock) as mock_get_object_response,
    ):
        mock_bucket_exists_response.return_value = True
        mock_get_object_response.return_value = {'Body': parquet_file_content}

        response = await client.get(f'/{pid}/data')
        return response
