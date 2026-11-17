"""The dashboard link: what it is scoped to, and who may use it (ANA-01).

The pilot's dashboard sits behind its own gateway: people sign in once at
Keycloak, the gateway verifies the access token and hands Superset the verified
name, and the platform tells the dashboard about every project with a shared
bridge token (`DASHBOARD_BRIDGE_TOKEN`) — a dashboard whose bridge refuses every
call when the token is empty.

The unified app keeps that rule on its own side, in the one place a link is
minted: a link carries its **scope** (project, AI-card version, target, tool) and
an **expiry**, and is signed with a shared key (`RESULTS_DASHBOARD_SECRET`) that
the dashboard (or the proxy in front of it) also holds. So a link is
authenticated — it cannot be minted or edited without the key — and scoped — the
signature covers exactly the scope, and a link for one project verifies for that
project only. **With no key configured no link is minted at all**: an
unauthenticated link to a dashboard holding every project's data is worse than no
link. The five answer shapes a verification can give are named here so a proxy
and a test can tell them apart.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode

from django.conf import settings

#: The query parameters that make up a link's scope, in the order they are signed.
SCOPE_PARAMS = ("project", "version", "target", "tool")
#: The signature and the expiry ride alongside the scope.
SIGNATURE_PARAM = "sig"
EXPIRY_PARAM = "exp"
#: What a refusal says; a proxy logs it and answers 403.
NO_SIGNATURE = "this link carries no signature"
BAD_SIGNATURE = "this link's signature does not match its scope"
EXPIRED = "this link has expired"
WRONG_PROJECT = "this link is not for this project"
NO_SECRET = "no dashboard link key is configured"


def signing_key() -> str:
    return (getattr(settings, "RESULTS_DASHBOARD_SECRET", "") or "").strip()


def _canonical(scope: dict, exp: int) -> str:
    """The exact bytes a signature covers: the scope in a fixed order, then the expiry."""
    parts = [f"{name}={scope.get(name) or ''}" for name in SCOPE_PARAMS]
    parts.append(f"{EXPIRY_PARAM}={exp}")
    return "\n".join(parts)


def _sign(canonical: str, key: str) -> str:
    return hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def sign_scope(scope: dict, *, key: str | None = None, now: float | None = None,
               ttl: int | None = None) -> dict | None:
    """The scope plus its expiry and signature, or None when no key is configured.

    Returning None (rather than an unsigned scope) is the fail-closed rule: a
    caller that gets None shows no link.
    """
    key = signing_key() if key is None else key
    if not key:
        return None
    if ttl is None:
        ttl = int(getattr(settings, "RESULTS_DASHBOARD_LINK_TTL", 900) or 900)
    exp = int((time.time() if now is None else now) + ttl)
    signed = {name: scope.get(name) for name in SCOPE_PARAMS}
    signed[EXPIRY_PARAM] = exp
    signed[SIGNATURE_PARAM] = _sign(_canonical(signed, exp), key)
    return signed


def build_link(base: str, scope: dict, *, key: str | None = None, now: float | None = None,
               ttl: int | None = None) -> str | None:
    """A signed, scoped link on `base`, or None when the bridge is closed or unsigned.

    Pure URL assembly: the dashboard reads its own store; nothing here queries it.
    """
    if not (base or "").strip():
        return None
    signed = sign_scope(scope, key=key, now=now, ttl=ttl)
    if signed is None:
        return None
    params = {name: signed[name] for name in SCOPE_PARAMS if signed.get(name)}
    params[EXPIRY_PARAM] = signed[EXPIRY_PARAM]
    params[SIGNATURE_PARAM] = signed[SIGNATURE_PARAM]
    base = base.strip()
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(params)}"


def verify_link(url: str, *, project: str | None = None, key: str | None = None,
                now: float | None = None) -> tuple[dict | None, str | None]:
    """`(scope, None)` for a good link, `(None, reason)` for a refusal.

    The order is deliberate: no signature first (nothing to check), then the
    signature (the link's own integrity), then the expiry, then the project the
    caller is asking about — so a link for another project is told apart from a
    forged one, and neither is mistaken for an expired one.
    """
    key = signing_key() if key is None else key
    if not key:
        return None, NO_SECRET
    _, _, query = (url or "").partition("?")
    params = dict(parse_qsl(query, keep_blank_values=True))
    if not params.get(SIGNATURE_PARAM):
        return None, NO_SIGNATURE
    try:
        exp = int(params.get(EXPIRY_PARAM, ""))
    except (TypeError, ValueError):
        return None, BAD_SIGNATURE
    scope = {name: params.get(name) for name in SCOPE_PARAMS}
    expected = _sign(_canonical(scope, exp), key)
    if not hmac.compare_digest(expected, params[SIGNATURE_PARAM]):
        return None, BAD_SIGNATURE
    if exp < int(time.time() if now is None else now):
        return None, EXPIRED
    if project is not None and str(project) != (scope.get("project") or ""):
        return None, WRONG_PROJECT
    return {**scope, EXPIRY_PARAM: exp}, None
