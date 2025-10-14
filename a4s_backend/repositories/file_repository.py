import logging
import boto3
from botocore.exceptions import ClientError
import os

from django.core.files import File

from config.settings import S3_USER, S3_PASSWORD, S3_URL


def upload_file(file: File, bucket: str, object_name: str = None) -> bool:

    if object_name is None:
        object_name = os.path.basename(file.name)

    # Upload the file
    if S3_USER and S3_PASSWORD:
        s3_client = boto3.client(
            "s3",
            endpoint_url=S3_URL,
            aws_access_key_id=S3_USER,
            aws_secret_access_key=S3_PASSWORD,
        )
    else:  # In production, use IAM roles instead of credentials
        s3_client = boto3.client("s3")
    try:
        response = s3_client.upload_fileobj(file, bucket, object_name)
    except ClientError as e:
        logging.error(e)
        return False
    return True