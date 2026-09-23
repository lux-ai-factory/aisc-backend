import enum
import uuid
from datetime import datetime
from typing import Any

from ninja import Schema
from pydantic import Field, model_validator

from aisc_backend.models.project_config import ProjectConfigCategory


class ProjectConfigValueType(str, enum.Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"


class ProjectConfigValueSchema(Schema):
    """Typed payload of a `variables` project config.

    Mirrors the plugin interface's ``ConfigValueType`` so that values can be
    matched to plugin-declared project config definitions (e.g.
    ``{"type": "number", "value": 42}``).
    """

    type: ProjectConfigValueType = ProjectConfigValueType.JSON
    value: Any = None


class ProjectConfigInSchema(Schema):
    category: ProjectConfigCategory = ProjectConfigCategory.VARIABLES
    key: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str = Field(min_length=1, max_length=255)
    value: str = ""
    json_value: ProjectConfigValueSchema | None = None

    @model_validator(mode="after")
    def _secret_requires_value(self) -> "ProjectConfigInSchema":
        if self.category is ProjectConfigCategory.SECRETS and not self.value:
            raise ValueError("value is required for secrets")
        return self


class ProjectConfigUpdateSchema(Schema):
    name: str | None = None
    value: str | None = None
    json_value: ProjectConfigValueSchema | None = None


class ProjectConfigOutSchema(Schema):
    pid: uuid.UUID
    category: str
    key: str
    name: str
    masked_value: str
    json_value: dict[str, Any]
    created_at: datetime
    updated_at: datetime
