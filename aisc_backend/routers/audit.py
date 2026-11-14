"""
The /audit endpoint: the HTTP door to the immudb AuditClerk for OTHER apps.

A caller POSTs an event here, FORWARDING the user's Keycloak token, and names the project the event belongs to
in X-AISC-Project: in the Configurator the project door admits only an editor of that project. KeycloakAuth
validates the token; the actor comes FROM the verified token (request.auth) and the app that wrote the event
from the Keycloak client that issued it (`azp`), never from the request body, so the caller cannot forge
either. `source_ip` is taken from the request too. Then we delegate to the single writer,
aisc_backend.audit.clerk.

POST /api/v1/audit  body: { action, resource_type, resource_id?, metadata?, outcome? } and nothing else
(Django's OWN code does NOT use this HTTP door: it calls clerk.write_event() / log_action() directly.)
"""
from typing import Literal

from ninja import Field, Router, Schema
from pydantic import ConfigDict

from aisc_backend.auth.keycloak import KeycloakAuth, require_role
from aisc_backend.audit.clerk import clerk
from aisc_backend.audit.log import actor_of, source_ip_of

# auth=KeycloakAuth() -> a valid Keycloak token is required (the caller forwards the user's token).
router = Router(tags=["audit"], auth=KeycloakAuth())


class AuditEventIn(Schema):
    """The body other apps POST: bounded, and no other field. No `actor`, `source_ip` or `source_app`: the
    server derives those from the request."""
    model_config = ConfigDict(extra="forbid")

    action: str = Field(max_length=64)                   # the verb: create | update | delete | answer | ...
    resource_type: str = Field(max_length=64)            # the object type: checklist | qualification | ...
    resource_id: str | None = Field(default=None, max_length=255)  # which object, optional
    metadata: dict = {}                                  # what actually changed / details
    outcome: Literal["ok", "failed"] = "ok"


def source_app_of(request) -> str:
    """The app that wrote an event: the Keycloak client that issued the caller's token (`azp`), as the
    project door read it or, without the door, as KeycloakAuth verified it; "unknown" without one."""
    claims = getattr(request, "aisc_claims", None)
    if not isinstance(claims, dict):
        claims = getattr(request, "auth", None)
    azp = claims.get("azp") if isinstance(claims, dict) else None
    return azp if isinstance(azp, str) and azp else "unknown"


@router.post("")
def post_audit(request, event: AuditEventIn):
    # actor, source_app and source_ip come from the request (verified token + connection), never the body.
    actor = actor_of(request)
    clerk.write_event(
        actor=actor,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        source_app=source_app_of(request),
        source_ip=source_ip_of(request),
        outcome=event.outcome,
        metadata=event.metadata,
    )
    return {"ok": True, "actor": actor}


@router.get("", auth=require_role("admin"))
def list_audit(request, limit: int = 100):
    """
    ADMIN-ONLY: read the audit log.
    Returns the newest events plus `verified`: whether a probe key verifies (VerifiedGet) against the state
    this process last saw, kept in memory since its start or its last login. It is not a proof that the
    log's rows were never changed. Gated by require_role("admin") (same as /me/admin) -> non-admins get 401/403.
    Reusing the Keycloak role gating: WRITING events takes an editor of the named project (the project door,
    in the Configurator); READING/VERIFYING the ledger is admin-only.
    """
    return {
        "verified": clerk.verify(),
        "events": clerk.list_events(limit=limit),
    }
