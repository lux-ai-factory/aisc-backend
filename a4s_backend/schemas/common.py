import uuid

from ninja import Schema


class UploadFileResponse(Schema):
    file_name: str


class RecordPid(Schema):
    pid: uuid.UUID