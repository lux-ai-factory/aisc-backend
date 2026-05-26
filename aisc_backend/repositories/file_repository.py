import logging
import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError
import os

from django.core.files import File

from config.settings import S3_USER, S3_PASSWORD, S3_URL

_s3_client: BaseClient | None = None

def _get_s3_client() -> BaseClient:
    global _s3_client
    if _s3_client is None:
        if S3_USER and S3_PASSWORD:
            _s3_client = boto3.client(
                "s3",
                endpoint_url=S3_URL,
                aws_access_key_id=S3_USER,
                aws_secret_access_key=S3_PASSWORD,
            )
        else:  # In production, use IAM roles instead of credentials
            _s3_client = boto3.client("s3")
    return _s3_client


def bucket_exists(bucket_name: str) -> bool:
    s3_client = _get_s3_client()
    try:
        response = s3_client.head_bucket(Bucket=bucket_name)
    except ClientError as e:
        logging.error(e)
        return False
    return True

def upload_file(file: File, bucket_name: str, object_name: str = None) -> bool:
    if object_name is None:
        object_name = os.path.basename(file.name)

    s3_client = _get_s3_client()
    try:
        response = s3_client.upload_fileobj(file, bucket_name, object_name)
    except ClientError as e:
        logging.error(e)
        return False
    return True

def get_object(bucket_name: str, object_name: str):
    s3_client = _get_s3_client()
    try:
        return s3_client.get_object(Bucket=bucket_name, Key=object_name)
    except ClientError as e:
        logging.error(e)
        return None