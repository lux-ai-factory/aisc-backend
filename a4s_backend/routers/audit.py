import uuid

from ninja import Router

from a4s_backend.schemas.audit import AuditEventListOutSchema, AuditEventOutSchema
from a4s_backend.services import audit_service

router = Router(tags=["audit"])


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
