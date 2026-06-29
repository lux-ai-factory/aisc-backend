"""
log_action — the in-process helper backend endpoints use to record an audit event.

Django's own actions call THIS directly (not the /audit HTTP door). It reads "who" from the verified
Keycloak token on the request and delegates to the single writer (clerk). It NEVER raises: an audit-log
failure (e.g. immudb briefly down) must not break the user's actual action — it's logged and swallowed.
"""
import logging

from .clerk import clerk

logger = logging.getLogger(__name__)


def _who(request) -> str:
    claims = getattr(request, "auth", None)
    if isinstance(claims, dict):                      # a verified token -> the real user
        return claims.get("preferred_username") or "unknown"
    return "unknown"                                  # auth disabled (True) or no claims


def log_action(request, what: str, consequence: dict | None = None,
               app: str = "backend", status: str = "ok") -> None:
    """Record an audit event for a backend action. Best-effort: never raises."""
    try:
        clerk.write_event(who=_who(request), what=what, app=app,
                           consequence=consequence or {}, status=status)
    except Exception as e:                            # noqa: BLE001 - audit must never break the action
        logger.warning("audit log failed for %r: %s", what, e)
