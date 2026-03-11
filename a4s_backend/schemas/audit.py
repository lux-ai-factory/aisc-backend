from datetime import datetime
from typing import Any

from ninja import Schema


class AuditEventOutSchema(Schema):
    id: int
    timestamp: datetime
    event_type: str
    user_id: int | None = None
    evaluation_id: str
    task_id: str
    plugin_name: str
    status: str
    duration_ms: int
    details: dict[str, Any] | None
    error_message: str
    verified: bool


class AuditEventListOutSchema(Schema):
    events: list[AuditEventOutSchema]
    count: int
