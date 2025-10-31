import uuid
from unittest.mock import patch, MagicMock, AsyncMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse, StreamingHttpResponse

from a4s_backend.routers.dataset import router, UploadDatasetFileResponse

from ninja.testing import TestAsyncClient

from a4s_backend.schemas.datashape import DataShapeInSchema, DataShapeOutSchema
from a4s_backend.schemas.feature import FeatureInSchema
from a4s_backend.utils.file_utils import csv_bytes_to_parquet_bytes

client = TestAsyncClient(router)

csv_file_content = b"feature1,feature2,target\n1,2,0\n3,4,1\n"
parquet_file_content = csv_bytes_to_parquet_bytes(csv_file_content)

# Mock the file_upload and eval auto_discover calls
async def upload_dataset_file(pid: uuid.UUID) -> UploadDatasetFileResponse:
    with (
        patch("a4s_backend.routers.dataset.file_repository.upload_file",
              new_callable=MagicMock) as mock_upload_response,
        patch("a4s_backend.routers.dataset.a4s_eval.autodiscover_datashape", new_callable=AsyncMock) as eval_response,
    ):
        mock_upload_response.return_value = True
        eval_response.return_value = HttpResponse(status=200)

        file = SimpleUploadedFile(
            "test_dataset.csv",
            csv_file_content,
            content_type="text/csv",
        )

        response = await client.put(
            f'/{pid}/data',
            FILES={"file": file},
        )
        upload_dataset_file_response = UploadDatasetFileResponse.model_construct(**response.data)
        return upload_dataset_file_response


async def patch_dataset_datashape(pid: uuid.UUID, features: list[FeatureInSchema]) -> DataShapeOutSchema:
    datashape_in_schema = DataShapeInSchema(features=features, date=None, target=None)

    response = await client.patch(f'/{pid}/datashape', json=datashape_in_schema.dict())
    datashape = DataShapeOutSchema.model_construct(**response.data)
    return datashape


async def get_dataset_datashape(pid: uuid.UUID) -> DataShapeOutSchema:
    response = await client.get(f'/{pid}/datashape')
    datashape = DataShapeOutSchema.model_construct(**response.data)
    return datashape

# Mock file_repository calls
async def get_dataset_file_data(pid: uuid.UUID) -> StreamingHttpResponse:
    with (
        patch("a4s_backend.routers.dataset.file_repository.bucket_exists", new_callable=MagicMock) as mock_bucket_exists_response,
        patch("a4s_backend.routers.dataset.file_repository.get_object", new_callable=MagicMock) as mock_get_object_response,
    ):
        mock_bucket_exists_response.return_value = True
        mock_get_object_response.return_value = {'Body': parquet_file_content}

        response = await client.get(f'/{pid}/data')
        return response
