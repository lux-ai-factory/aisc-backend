import uuid
from datetime import datetime
from typing import Any

from ninja import Schema
from pydantic import Field

from aisc_backend.models.project_setting import ProjectSettingCategory


class ProjectSettingInSchema(Schema):
    category: ProjectSettingCategory = ProjectSettingCategory.GENERAL
    key: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str = Field(min_length=1, max_length=255)
    value: str = ""
    json_value: dict[str, Any] = {}


class ProjectSettingUpdateSchema(Schema):
    key: str | None = Field(default=None, max_length=255, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str | None = None
    value: str | None = None
    json_value: dict[str, Any] | None = None


class ProjectSettingOutSchema(Schema):
    pid: uuid.UUID
    category: str
    key: str
    name: str
    masked_value: str
    json_value: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DeriveFeaturesSchema(Schema):
    dataset_pid: uuid.UUID
    name: str = Field(min_length=1, max_length=255)


class ValidateDatashapeSchema(Schema):
    dataset_pid: uuid.UUID
