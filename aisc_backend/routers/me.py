"""
A tiny PROTECTED endpoint used to prove Keycloak auth end-to-end (and as a test target).

GET /api/v1/me        -> requires any valid Keycloak token; returns who you are + your roles.
GET /api/v1/me/admin  -> requires the `admin` role; proves role gating (403/401 for primary-user).
"""
from ninja import Router

from aisc_backend.auth.keycloak import KeycloakAuth, require_role, get_roles

# auth=KeycloakAuth() on the whole router -> every endpoint here needs a valid token.
router = Router(tags=["me"], auth=KeycloakAuth())


@router.get("")
def me(request):
    """Return the current user's identity + roles, read from the verified token (request.auth)."""
    claims = request.auth
    if claims is True:  # auth disabled (bypass) -> no real user behind the request
        return {"username": None, "roles": [], "auth_disabled": True}
    return {"username": claims.get("preferred_username"), "roles": get_roles(claims)}


@router.get("/admin", auth=require_role("admin"))
def me_admin(request):
    """Only reachable with the `admin` role (the gate is enforced by require_role on the route)."""
    claims = request.auth
    username = claims.get("preferred_username") if isinstance(claims, dict) else None
    return {"ok": True, "username": username}
