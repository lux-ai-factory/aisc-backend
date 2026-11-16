"""
The project-membership door for Django-Ninja endpoints.

Ports the roles/access semantics the platform's routes use: `effective_role`
(admin counts as owner), `role_or_404` (a non-member is told nothing -> 404;
a member who lacks the needed rank -> 403), `owner_or_403`, and
`refuse_losing_last_owner`. Identity comes from the verified Keycloak claims on
`request.auth` (or the development caller when auth is disabled), never from a
request body.
"""
from ninja.errors import HttpError

from aisc_backend.auth.caller import Caller, caller_from_claims, development_caller
from aisc_backend.auth.membership import at_least, may_manage_members
from aisc_backend.models.project import Project
from aisc_backend.repositories.membership_repository import MembershipRepository

#: The realm role that administers the platform. It is not a membership: an
#: admin is not in the project, it may act on any of them, which is what makes
#: an orphaned project recoverable.
ADMIN_ROLE = "admin"

membership = MembershipRepository()


def caller_of(request) -> Caller:
    """Who is calling, from the verified token on request.auth.

    KeycloakAuth puts the verified claims dict on `request.auth`; while auth is
    disabled (AUTH_ENABLED false) it is True, so we return the fixed
    development caller — a token-less local run still has a stable subject to
    key membership on.
    """
    claims = getattr(request, "auth", None)
    if isinstance(claims, dict):
        try:
            return caller_from_claims(claims)
        except ValueError:
            # a token that verified but named no subject: treat as absent
            return development_caller()
    return development_caller()


def caller_subject_of(request) -> str | None:
    """The stable subject of the caller, for operations that only need the id."""
    try:
        return caller_of(request).subject
    except ValueError:
        return None


async def effective_role(project: Project, caller: Caller) -> str | None:
    """What this caller is to this project, counting the admin role."""
    if caller.has_role(ADMIN_ROLE):
        return "owner"
    return await membership.role_in_project(project, caller.subject)


async def role_or_404(request, project: Project, needed: str = "viewer") -> str:
    """The caller's role, or the answer a stranger gets.

    404 for "not yours" and 403 for "not enough": a member is told they lack
    the rank, a non-member is told nothing at all.
    """
    caller = caller_of(request)
    role = await effective_role(project, caller)
    if not at_least(role, "viewer"):
        raise HttpError(404, f"no project {project.pid}")
    if not at_least(role, needed):
        raise HttpError(403, f"this takes {needed} on this project")
    return role


async def owner_or_403(request, project: Project) -> str:
    """Only an owner decides who is in a project."""
    role = await role_or_404(request, project, "owner")
    return role


async def refuse_losing_last_owner(project: Project, subject: str) -> None:
    """A project with nobody in it is a project nobody can open, and the last
    owner leaving is the only way to make one."""
    if await membership.is_owner(project, subject) and await membership.owner_count(project) <= 1:
        raise HttpError(409, "a project keeps at least one owner")


__all__ = [
    "ADMIN_ROLE",
    "Caller",
    "caller_of",
    "caller_subject_of",
    "effective_role",
    "may_manage_members",
    "owner_or_403",
    "refuse_losing_last_owner",
    "role_or_404",
]
