"""
The /audit endpoint — the HTTP door to the immudb AuditClerk for OTHER apps.

controls / qualification (and any caller) POST an event here, FORWARDING the user's Keycloak token.
KeycloakAuth validates the token; we read the actor FROM the verified token (request.auth) — never from
the request body — so the caller cannot forge who did it. `source_ip` is taken from the request too. Then
we delegate to the single writer, aisc_backend.audit.clerk.

POST /api/v1/audit  body: { action, resource_type, resource_id?, source_app, metadata?, outcome? }
(Django's OWN code does NOT use this HTTP door — it calls clerk.write_event() / log_action() directly.)
"""
from ninja import Router, Schema

from aisc_backend.auth.keycloak import KeycloakAuth, require_role
from aisc_backend.audit.clerk import clerk
from aisc_backend.audit.log import actor_of, source_ip_of

# auth=KeycloakAuth() -> a valid Keycloak token is required (the caller forwards the user's token).
router = Router(tags=["audit"], auth=KeycloakAuth())


class AuditEventIn(Schema):
    """The body other apps POST. NOTE: no `actor`/`source_ip` — the server derives those from the request."""
    action: str                    # the verb: create | update | delete | answer | generate | ...
    resource_type: str             # the object type: checklist | qualification | source | ...
    resource_id: str | None = None # which object, optional
    source_app: str                # which app: controls | qualification | webapp
    metadata: dict = {}            # what actually changed / details
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
        outcome=event.outcome,
        metadata=event.metadata,
    )
    return {"ok": True, "actor": actor}


@router.get("", auth=require_role("admin"))
def list_audit(request, limit: int = 100):
    """
    ADMIN-ONLY: read + verify the tamper-proof audit log.
    Returns the newest events plus `verified` — the cryptographic proof (VerifiedGet) that the ledger
    was not tampered with. Gated by require_role("admin") (same as /me/admin) -> non-admins get 401/403.
    Reusing the Keycloak role gating: WRITING events is open to any authenticated caller; READING/VERIFYING
    the ledger is admin-only.
    """
    return {
        "verified": clerk.verify(),
        "events": clerk.list_events(limit=limit),
    }
