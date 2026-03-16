import csv
import io
import uuid

from django.http import HttpResponse
from ninja import Router

from a4s_backend.schemas.audit import AuditEventListOutSchema, AuditEventOutSchema
from a4s_backend.services import audit_service

router = Router(tags=["audit"])

_CSV_COLUMNS = [
    "id",
    "timestamp",
    "event_type",
    "user_id",
    "evaluation_id",
    "task_id",
    "plugin_name",
    "status",
    "duration_ms",
    "test_set",
    "configuration",
    "target_system",
    "execution_start",
    "execution_end",
    "error_message",
    "details",
]


@router.get("/events", response=AuditEventListOutSchema)
async def list_audit_events(
    request,
    evaluation_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List audit events with optional filters. Standard (non-verified) reads."""
    events = await audit_service.get_audit_events(
        evaluation_id=evaluation_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    count = await audit_service.count_audit_events(
        evaluation_id=evaluation_id,
        event_type=event_type,
    )
    return {"events": events, "count": count}


@router.get("/events/{event_id}/verified", response=AuditEventOutSchema)
async def get_verified_audit_event(request, event_id: int):
    """Get a single audit event with cryptographic verification.

    Uses immudb's verifiableSQLGet to prove the record hasn't been
    tampered with. The `verified` field in the response indicates
    whether the cryptographic proof passed.
    """
    return await audit_service.get_verified_audit_event(event_id)


@router.get("/evaluations/{evaluation_pid}/events", response=list[AuditEventOutSchema])
async def get_evaluation_audit_events(request, evaluation_pid: uuid.UUID):
    """Get all audit events for a specific evaluation."""
    return await audit_service.get_audit_events_for_evaluation(str(evaluation_pid))


@router.get("/events/export/csv")
async def export_audit_events_csv(
    request,
    evaluation_id: str | None = None,
    event_type: str | None = None,
):
    """Export audit events as a CSV file."""
    events = await audit_service.get_audit_events(
        evaluation_id=evaluation_id,
        event_type=event_type,
        limit=10000,
        offset=0,
    )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        row = dict(event)
        if row.get("details"):
            import json

            row["details"] = json.dumps(row["details"])
        writer.writerow(row)

    response = HttpResponse(buf.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit_events.csv"'
    return response


@router.get("/events/export/json")
async def export_audit_events_json(
    request,
    evaluation_id: str | None = None,
    event_type: str | None = None,
):
    """Export audit events as a downloadable JSON file."""
    import json
    from datetime import datetime

    events = await audit_service.get_audit_events(
        evaluation_id=evaluation_id,
        event_type=event_type,
        limit=10000,
        offset=0,
    )

    def _serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    body = json.dumps(list(events), default=_serialize, indent=2)
    response = HttpResponse(body, content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="audit_events.json"'
    return response
