from typing import Any

from pydantic import BaseModel, Field


class TaskProgress(BaseModel):
    progress: float = Field(..., ge=0.0, le=1.0, description="Progress between 0 and 1")
    extra: dict[str, Any] = Field(default_factory=dict, description="Plugin-defined extra data")
