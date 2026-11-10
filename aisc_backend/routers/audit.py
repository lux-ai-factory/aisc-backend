"""
The /audit endpoints — the HTTP door to the immudb AuditClerk.

Other apps (controls, qualification, a future webapp) POST an event here,
FORWARDING the user's Keycloak token. KeycloakAuth validates the token; we read
the actor FROM the verified token (request.auth) — never from the request body
— so the caller cannot forge who did it. `source_ip` is taken from the request
too. Then we delegate to the single writer, aisc_backend.audit.clerk.

POST /api/v1/audit  body: { action, resource_type, resource_id?, source_app,
                            metadata?, outcome?, project_id? }
(Django's OWN code does not use this HTTP door — it calls log_action()
directly.)

GET  /api/v1/audit              admin-only: every event + tamper check.
GET  /api/v1/audit/project/{pid} any member of that project: only its events.
"""
import uuid

from ninja import Router, Schema

from aisc_backend.audit.clerk import clerk
from aisc_backend.audit.log import actor_of, project_id_of, source_ip_of
from aisc_backend.auth.keycloak import KeycloakAuth, require_role
from aisc_backend.auth.access import role_or_404

# auth=KeycloakAuth() -> a valid Keycloak token is required (the caller forwards the user's token).
router = Router(tags=["audit"], auth=KeycloakAuth())


class AuditEventIn(Schema):
    """The body other apps POST. NOTE: no `actor`/`source_ip` — the server derives those from the request."""
    action: str                    # the verb: create | update | delete | answer | generate | ...
    resource_type: str             # the object type: checklist | qualification | source | ...
    resource_id: str | None = None # which object, optional
    source_app: str                # which app: controls | qualification | webapp
    metadata: dict = {}            # what actually changed / details; may carry `projectPid`
    outcome: str = "ok"            # ok | failed


@router.post("")
def post_audit(request, event: AuditEventIn):
    # actor + source_ip come from the request (verified token + connection) — never the body.
    actor = actor_of(request)
    clerk.write_event(
        actor=actor,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        source_app=event.source_app,
        source_ip=source_ip_of(request),
        project_id=project_id_of(event.metadata),
        outcome=event.outcome,
        metadata=event.metadata,
    )
    return {"ok": True, "actor": actor}


@router.get("", auth=require_role("admin"))
def list_audit(request, limit: int = 100):
    """ADMIN-ONLY: read + verify the tamper-proof audit log.

    Returns the newest events plus `verified` — the cryptographic proof
    (VerifiedGet) that the ledger was not tampered with. Writing events is open
    to any authenticated caller; reading the whole ledger is admin-only.
    """
    return {
        "verified": clerk.verify(),
        "events": clerk.list_events(limit=limit),
    }


@router.get("/project/{pid}", response=dict)
async def list_project_audit(request, pid: uuid.UUID):
    """A project's own events, for any member of that project.

    404 for a stranger (so project names do not leak), 200 with the project's
    events for a member. No `verified` — that proof is the admin's.
    """
    from aisc_backend.repositories.base_repository import BaseRepository
    from aisc_backend.models.project import Project

    project_repository = BaseRepository(model=Project)
    project = await project_repository.get(pid)
    await role_or_404(request, project)
    return {"events": clerk.list_events(limit=1000, project_id=str(pid))}
