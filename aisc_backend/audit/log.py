"""
log_action — the in-process helper backend endpoints use to record an audit event.

Django's own actions call THIS directly (not the /audit HTTP door). It reads the actor from the verified
Keycloak token and the client IP from the request, then delegates to the single writer (clerk). It NEVER
raises: an audit-log failure (e.g. immudb briefly down) must not break the user's actual action.
"""
import logging

from .clerk import clerk

logger = logging.getLogger(__name__)


def actor_of(request) -> str:
    """WHO: the verified user from the Keycloak token (or 'unknown' when auth is disabled/absent)."""
    claims = getattr(request, "auth", None)
    if isinstance(claims, dict):                      # a verified token -> the real user
        return claims.get("preferred_username") or "unknown"
    return "unknown"                                  # auth disabled (True) or no claims


def source_ip_of(request) -> str | None:
    """WHERE FROM: the client IP, honouring a proxy's X-Forwarded-For (first hop) when present."""
    meta = getattr(request, "META", {}) or {}
    xff = meta.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return meta.get("REMOTE_ADDR")


def log_action(request, action: str, resource_type: str, resource_id=None,
               metadata: dict | None = None, source_app: str = "backend",
               outcome: str = "ok") -> None:
    """
    Record an audit event for a backend action. Best-effort: never raises.
      action        - the verb (create | update | delete | run | upload | ...)
      resource_type - the object type (project | dataset | evaluation | plugin | ...)
      resource_id   - which object (id/pid), optional
      metadata      - dict of what changed / details
    """
    try:
        clerk.write_event(
            actor=actor_of(request),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            source_app=source_app,
            source_ip=source_ip_of(request),
            outcome=outcome,
            metadata=metadata or {},
        )
    except Exception as e:                            # noqa: BLE001 - audit must never break the action
        logger.warning("audit log failed for %s:%s: %s", resource_type, action, e)
