"""
Keycloak token verification for the backend.

THREE pieces (you write the bodies — reference: keycloak/verify_token.py, the proven spike):
  1. verify_token(token) -> dict   : framework-agnostic core. The future "parent" — if eval (or
                                     any other Python service) ever needs this, lift THIS function
                                     into shared/ and the ninja class below keeps working unchanged.
  2. KeycloakAuth(HttpBearer)      : the django-ninja adapter ("child"). Any logged-in user.
  3. require_role(role)            : KeycloakAuth + a realm-role check (for admin-only endpoints).

Auth is gated behind settings.AUTH_ENABLED so the app keeps working until you flip it on.
"""
from __future__ import annotations

import logging

import jwt
from jwt import PyJWKClient
from django.conf import settings
from ninja.errors import HttpError
from ninja.security import HttpBearer

from aisc_backend import deployment

logger = logging.getLogger(__name__)

# --- config (added to config/settings.py in step 2) ---
KEYCLOAK_JWKS_URL = getattr(settings, "KEYCLOAK_JWKS_URL", "")
KEYCLOAK_ISSUER = getattr(settings, "KEYCLOAK_ISSUER", "")
AUTH_ENABLED = getattr(settings, "AUTH_ENABLED", False)

# Cache the JWKS client so Keycloak's public keys are fetched once, not per request.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Lazily build + cache the JWKS client. (Patched in unit tests.)"""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(KEYCLOAK_JWKS_URL)
    return _jwks_client


def verify_token(token: str) -> dict:
    """
    Verify a Keycloak access token; return its claims (a dict).
    Raise jwt.PyJWTError (or a subclass) if invalid / expired / tampered / wrong issuer.
    """
    key = _get_jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=["RS256"], issuer=KEYCLOAK_ISSUER, options={"verify_aud": False})


def get_roles(claims: dict) -> list[str]:
    """
    Return the realm roles from verified claims.
    """
    return claims.get("realm_access", {}).get("roles", [])

#: The header oauth2-proxy sets when it has vouched for a request. In the Configurator the
#: gateway holds the session, refreshes it, and knows who is behind the request; a page behind it
#: therefore need not keep a second session of its own, and when it does not, this is where the
#: token arrives. Standalone has its own login and never reads it (the door names it in both).
GATEWAY_TOKEN_HEADER = "X-Auth-Request-Access-Token"


class KeycloakAuth(HttpBearer):
    """
    django-ninja bearer auth. `token` is the string after 'Bearer ' (ninja extracts it).
    Contract: return TRUTHY -> request passes (value becomes request.auth); return None -> 401.

    In the Configurator the token may also arrive in the gateway's header, from the session the
    gateway already has. Both are the same session and are verified the same way; a request
    carrying neither is refused.
    """

    def __call__(self, request):
        found = super().__call__(request)
        if found is not None or not deployment.is_configurator():
            return found
        token = request.headers.get(GATEWAY_TOKEN_HEADER, "")
        return self.authenticate(request, token) if token else None

    def authenticate(self, request, token):
        # if authenticator is disabled, allow all requests
        if not AUTH_ENABLED:
            return True

        try:
            claims = verify_token(token)
        except jwt.PyJWKClientError as e:
            # JWKS / key-fetch problem (Keycloak unreachable, or no matching key) — an INFRA issue
            logger.warning("Keycloak JWKS error while verifying token: %s", e)
            return None                    # -> 401
        except jwt.PyJWTError as e:
            # the token itself is invalid / expired / tampered / wrong issuer — a CLIENT issue
            logger.info("Rejected token: %s", e)
            return None                    # -> 401

        return claims                      # ninja sets request.auth = claims (read it in the endpoint)




def require_role(role: str) -> KeycloakAuth:
    """
    Return a ninja auth instance that requires a valid token AND the given realm role.
    Usage on an admin-only endpoint:  @router.get("/x", auth=require_role("admin"))
    Normal endpoints just use KeycloakAuth() (any logged-in user = admin OR primary-user).
    """

    class _RoleRequired(KeycloakAuth):
        def authenticate(self, request, token):
            claims = super().authenticate(request, token)   # first: normal token check
            # pass through the bypass (True, auth disabled) or a failed auth (None) as-is
            if claims is True or claims is None:
                return claims
            # enforce the role
            if role in get_roles(claims):
                return claims
            # A verified token that lacks the role is 403, not 401 (both modes). 401 invites the
            # client to sign in again, which cannot help, and it makes a permissions problem read
            # as a session problem in the logs.
            raise HttpError(403, f"this needs the {role!r} role")

    return _RoleRequired()


def router_auth():
    """The auth a router names for itself.

    In the Configurator a router names the bearer check as well as the API-wide default, so that
    `request.auth` is the verified claims for that router on its own: that is what the membership
    checks read. Standalone keeps Sean's routers, which inherit the API's default (NOT_SET).
    """
    from ninja.constants import NOT_SET

    return KeycloakAuth() if deployment.is_configurator() else NOT_SET


def admin_in_configurator():
    """Route auth for installing, reinstalling or removing a plugin.

    In the Configurator an install fetches a distribution and the engine then runs its code on a
    shared server, so it takes the admin role. Standalone keeps Sean's rule: any signed-in user
    (the API's default, NOT_SET).
    """
    from ninja.constants import NOT_SET

    return require_role("admin") if deployment.is_configurator() else NOT_SET
