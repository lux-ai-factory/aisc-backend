from django.http import StreamingHttpResponse
from ninja import Router
from ninja.errors import HttpError
from a4s_backend.repositories import file_repository
from config.settings import S3_DATASETS_BUCKET, S3_MODELS_BUCKET

router = Router(tags=["file"])

async def _get_s3_file_stream(bucket_name: str, file_name: str) -> StreamingHttpResponse:
    try:
        if not file_repository.bucket_exists(bucket_name):
            raise HttpError(500, f"Bucket {bucket_name} not found")

        response = file_repository.get_object(bucket_name=bucket_name, object_name=file_name)
        return StreamingHttpResponse(response["Body"])
    except HttpError:
        raise
    except Exception as e:
        raise HttpError(500, f"Error fetching file from {bucket_name}: {str(e)}")


@router.get("/dataset/{file_name}")
async def get_dataset_file(request, file_name: str):
    return await _get_s3_file_stream(S3_DATASETS_BUCKET, file_name)


@router.get("/model/{file_name}")
async def get_model_file(request, file_name: str):
    return await _get_s3_file_stream(S3_MODELS_BUCKET, file_name)
