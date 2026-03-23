"""Auth router — exposes /api/v1/auth/me for the frontend."""

from asgiref.sync import sync_to_async
from ninja import Router, Schema

from a4s_backend.auth import require_auth

router = Router(tags=["auth"])


class UserMeOut(Schema):
    id: int
    email: str
    username: str
    roles: list[str]
    is_admin: bool


@router.get("/me", response=UserMeOut, auth=[require_auth])
async def me(request):
    user = request.user
    roles = await sync_to_async(list)(user.groups.values_list("name", flat=True))
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "roles": roles,
        "is_admin": "admin" in roles,
    }
