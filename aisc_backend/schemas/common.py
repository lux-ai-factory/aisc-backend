import uuid

from ninja import Schema

class RecordPid(Schema):
    pid: uuid.UUID
