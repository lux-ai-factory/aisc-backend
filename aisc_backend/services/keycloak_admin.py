"""
Client for Keycloak's Admin REST API.

The webapp manages users and their access through the backend; the backend
talks to Keycloak here — a confidential service-account client doing the
client-credentials grant for an admin token, then CRUD on
`/admin/realms/{realm}/users` and realm roles. The browser never sees the
secret: the backend holds `KEYCLOAK_ADMIN_CLIENT_SECRET` (from the env by
scripts/secrets.sh, never committed) and forwards nothing.

This module is the one place the backend talks to Keycloak as an operator, so
a test can stub it (contract-first) without a running realm. When the admin
client is not configured, the methods fail closed (raise/return empty) so the
backend works in dev and in tests without Keycloak.
"""
from __future__ import annotations

import logging
import time

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is optional at import time
    httpx = None

from config.settings import (
    KEYCLOAK_ADMIN_CLIENT_ID,
    KEYCLOAK_ADMIN_CLIENT_SECRET,
    KEYCLOAK_ADMIN_URL,
    KEYCLOAK_REALM,
)

logger = logging.getLogger(__name__)

#: How long (seconds) to cache a Keycloak admin access token before re-granting.
TOKEN_TTL = 300


class KeycloakAdminError(RuntimeError):
    """The realm refused an admin operation."""


class KeycloakAdminUnavailable(KeycloakAdminError):
    """Not reachable, misconfigured, or refused (401/403/5xx)."""


class TokenBucket:
    """One admin token per process, refreshed before it expires.

    Keycloak's admin access tokens are short-lived; a long-running backend asks
    for a new one at most once per `ttl`, never per request.
    """

    def __init__(self, now: float | None = None):
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._now = now

    def _time(self) -> float:
        return time.monotonic() if self._now is None else self._now()

    def get(self) -> str | None:
        if self._token is not None and self._time() < self._expires_at:
            return self._token
        return None

    def put(self, token: str, ttl: int) -> None:
        self._token = token
        self._expires_at = self._time() + ttl

    def clear(self) -> None:
        self._token = None
        self._expires_at = 0.0


token_bucket = TokenBucket()


def _configured() -> bool:
    return bool(KEYCLOAK_ADMIN_URL and KEYCLOAK_ADMIN_CLIENT_SECRET)


async def _admin_headers() -> dict[str, str]:
    """An Authorization header with a fresh admin token."""
    token = token_bucket.get()
    if token is None:
        token = await _grant_token()
        token_bucket.put(token, TOKEN_TTL)
    return {"Authorization": f"Bearer {token}"}


async def _grant_token() -> str:
    """Exchange the service account's credentials for an admin access token."""
    if not _configured() or httpx is None:
        raise KeycloakAdminUnavailable("the Keycloak admin client is not configured")
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"{KEYCLOAK_ADMIN_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": KEYCLOAK_ADMIN_CLIENT_ID,
                "client_secret": KEYCLOAK_ADMIN_CLIENT_SECRET,
            },
        )
    if resp.status_code != 200:
        raise KeycloakAdminUnavailable(f"Keycloak token grant answered {resp.status_code}")
    token = resp.json().get("access_token")
    if not token:
        raise KeycloakAdminUnavailable("the Keycloak token grant returned no access token")
    return token


def _users_url() -> str:
    return f"{KEYCLOAK_ADMIN_URL}/admin/realms/{KEYCLOAK_REALM}/users"


async def _call(
    method: str, path: str, json: dict | None = None, params: dict | None = None
) -> httpx.Response:
    if not _configured() or httpx is None:
        raise KeycloakAdminUnavailable("the Keycloak admin client is not configured")
    headers = await _admin_headers()
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.request(
                method, f"{KEYCLOAK_ADMIN_URL}{path}", json=json, params=params, headers=headers
            )
    except httpx.HTTPError as exc:
        raise KeycloakAdminUnavailable(
            f"Keycloak admin unreachable ({type(exc).__name__})"
        ) from None
    if resp.status_code >= 400:
        raise KeycloakAdminError(f"Keycloak admin answered {resp.status_code}")
    return resp


# --- user CRUD -------------------------------------------------------------------


async def list_users(search: str | None = None, first: int = 0, max: int = 100) -> list[dict]:
    """Users in the realm, optionally filtered by `search` (email/name/subject)."""
    if not _configured():
        return []
    params = {"max": str(max), "first": str(first)}
    if search:
        params["search"] = search
    resp = await _call("GET", _users_url(), params=params)
    return resp.json()


async def get_user(user_id: str) -> dict | None:
    """One user by its Keycloak id (not the subject)."""
    if not _configured():
        return None
    resp = await _call("GET", f"{_users_url()}/{user_id}")
    return resp.json()


async def find_by_subject(subject: str) -> dict | None:
    """The Keycloak user whose `username`/email search matches a subject.

    The backend keys membership on Keycloak's `sub`; Keycloak itself does not
    expose a lookup-by-sub, so the search is the closest stable match and the
    caller re-checks the result before trusting it.
    """
    users = await list_users(search=subject, max=1)
    return users[0] if users else None


async def create_user(payload: dict) -> dict:
    """Create a user; the realm answers the Location of the new user's id."""
    if not _configured():
        raise KeycloakAdminUnavailable("the Keycloak admin client is not configured")
    resp = await _call("POST", _users_url(), json=payload)
    location = resp.headers.get("location", "")
    user_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
    return {"id": user_id}


async def update_user(user_id: str, payload: dict) -> None:
    """Update name/email/enabled (any writable user attribute)."""
    await _call("PUT", f"{_users_url()}/{user_id}", json=payload)


async def reset_password(user_id: str, password: str, temporary: bool = True) -> None:
    """Set a user's password (temporary by default — forced change next login)."""
    await _call(
        "PUT",
        f"{_users_url()}/{user_id}/reset-password",
        json={"type": "password", "value": password, "temporary": temporary},
    )


# --- realm roles -----------------------------------------------------------------


async def assign_realm_role(user_id: str, role: str) -> None:
    """Give a realm role (e.g. `admin`) to a user."""
    await _call("POST", f"{_users_url()}/{user_id}/role-mappings/realm", json=[{"name": role}])


async def revoke_realm_role(user_id: str, role: str) -> None:
    """Remove a realm role from a user."""
    await _call("DELETE", f"{_users_url()}/{user_id}/role-mappings/realm", json=[{"name": role}])
