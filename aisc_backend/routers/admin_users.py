"""
Keycloak user administration, under /admin/users/*.

ADMIN-ONLY: every route requires the realm `admin` role (require_role at the
router level). The webapp manages users and their access through these — the
backend talks to Keycloak's Admin REST API (confidential service-account
client, see services.keycloak_admin) and never exposes the admin secret or
any user's password to the browser.

Writes are audited (action + resource_type `keycloak_user`, and `keycloak_role`
for role changes). The immudb ledger keeps the audit trail of who changed
which user — it cannot be edited afterwards.
"""
from asgiref.sync import sync_to_async
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.keycloak import require_role
from aisc_backend.services import keycloak_admin

router = Router(tags=["admin-users"], auth=require_role("admin"))


class UserIn(Schema):
    username: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    enabled: bool = True


class UserUpdateIn(Schema):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    enabled: bool | None = None


class PasswordIn(Schema):
    password: str
    temporary: bool = True


class RoleIn(Schema):
    role: str


async def _audit(request, action: str, resource_type: str, resource_id: str, **extra):
    await sync_to_async(log_action)(
        request,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=extra,
    )


@router.get("")
async def list_users(request, search: str | None = None):
    """Search the realm's users (by email/name/subject)."""
    return await keycloak_admin.list_users(search=search)


@router.get("/{user_id}")
async def get_user(request, user_id: str):
    user = await keycloak_admin.get_user(user_id)
    if user is None:
        raise HttpError(404, "no such user")
    return user


@router.post("", response={201: dict})
async def create_user(request, data: UserIn):
    payload = {
        "username": data.username.lower(),
        "email": data.email or None,
        "firstName": data.first_name,
        "lastName": data.last_name,
        "enabled": data.enabled,
        "emailVerified": True,
    }
    created = await keycloak_admin.create_user(payload)
    await _audit(request, "create", "keycloak_user", created.get("id", ""), **payload)
    return 201, created


@router.patch("/{user_id}")
async def update_user(request, user_id: str, data: UserUpdateIn):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    if not payload:
        raise HttpError(422, "nothing to update")
    await keycloak_admin.update_user(user_id, payload)
    await _audit(request, "update", "keycloak_user", user_id, **payload)
    return await keycloak_admin.get_user(user_id)


@router.post("/{user_id}/reset-password")
async def reset_password(request, user_id: str, data: PasswordIn):
    if not data.password:
        raise HttpError(422, "a password is required")
    await keycloak_admin.reset_password(user_id, data.password, data.temporary)
    await _audit(
        request, "reset_password", "keycloak_user", user_id, temporary=data.temporary,
    )
    return {"ok": True}


@router.post("/{user_id}/roles/{role}")
async def assign_role(request, user_id: str, role: str):
    await keycloak_admin.assign_realm_role(user_id, role)
    await _audit(request, "assign_role", "keycloak_role", f"{user_id}:{role}", role=role)
    return {"ok": True}


@router.delete("/{user_id}/roles/{role}", response={204: None})
async def revoke_role(request, user_id: str, role: str):
    await keycloak_admin.revoke_realm_role(user_id, role)
    await _audit(request, "revoke_role", "keycloak_role", f"{user_id}:{role}", role=role)
    return 204, None
