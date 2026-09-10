import uuid
from datetime import datetime
from typing import Any

from ninja import Schema
from pydantic import Field

from aisc_backend.models.project_config import ProjectConfigCategory


class ProjectConfigInSchema(Schema):
    category: ProjectConfigCategory = ProjectConfigCategory.VARIABLES
    key: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str = Field(min_length=1, max_length=255)
    value: str = ""
    json_value: dict[str, Any] = {}
    endpoint_type: str = ""
    url: str = ""


class ProjectConfigUpdateSchema(Schema):
    key: str | None = Field(default=None, max_length=255, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str | None = None
    value: str | None = None
    json_value: dict[str, Any] | None = None
    endpoint_type: str | None = None
    url: str | None = None


class ProjectConfigOutSchema(Schema):
    pid: uuid.UUID
    category: str
    key: str
    name: str
    masked_value: str
    json_value: dict[str, Any]
    endpoint_type: str
    url: str
    created_at: datetime
    updated_at: datetime


class DeriveFeaturesSchema(Schema):
    dataset_pid: uuid.UUID
    name: str = Field(min_length=1, max_length=255)


class ValidateDatashapeSchema(Schema):
    dataset_pid: uuid.UUID
