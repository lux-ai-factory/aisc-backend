"""
The /audit endpoint — the HTTP door to the immudb AuditClerk for OTHER apps.

controls / qualification (and any caller) POST an event here, FORWARDING the user's Keycloak token.
KeycloakAuth validates the token; we read the acting user ("who") FROM the verified token (request.auth)
— never from the request body — so the caller cannot forge who did it. Then we delegate to the single
writer, aisc_backend.audit.clerk.

POST /api/v1/audit   body: { what, app, consequence?, status? }   -> writes one immutable audit event.
(Django's OWN code does NOT use this HTTP door — it calls clerk.write_event() directly.)
"""
from ninja import Router, Schema

from aisc_backend.auth.keycloak import KeycloakAuth
from aisc_backend.audit.clerk import clerk

# auth=KeycloakAuth() -> a valid Keycloak token is required (the caller forwards the user's token).
router = Router(tags=["audit"], auth=KeycloakAuth())


class AuditEventIn(Schema):
    """The body other apps POST. NOTE: there is no `who` field — identity comes from the token."""
    what: str                      # "object:verb", e.g. "checklist:answer"
    app: str                       # which app: controls | qualification | webapp | backend
    consequence: dict = {}         # what actually changed
    status: str = "ok"             # ok | failed


@router.post("")
def post_audit(request, event: AuditEventIn):
    claims = request.auth
    # "who" is read from the verified token; True = auth disabled (dev bypass) -> no real user.
    who = claims.get("preferred_username") if isinstance(claims, dict) else None
    clerk.write_event(
        who=who or "unknown",
        what=event.what,
        app=event.app,
        consequence=event.consequence,
        status=event.status,
    )
    return {"ok": True, "who": who}
