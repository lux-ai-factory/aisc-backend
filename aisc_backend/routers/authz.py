"""
The /authz surface — the one endpoint the other (isolated) apps call to decide
what a caller may do inside a project.

Matches the reference contract: `GET /authz/projects/{slug}` answers for
strangers too (role=None is a valid answer, not an error), because the apps
ask it before deciding what to show. Admin is returned alongside the role.
"""
import uuid

from django.core.exceptions import ObjectDoesNotExist
from ninja import Router, Schema
from ninja.errors import HttpError

from aisc_backend.auth.access import caller_of, effective_role
from aisc_backend.auth.membership import may_write
from aisc_backend.repositories.project_repository import ProjectRepository

router = Router(tags=["authz"])

project_repository = ProjectRepository()


class AuthzResponse(Schema):
    role: str | None = None
    admin: bool = False
    may_write: bool = False


@router.get("/projects/{slug}", response=AuthzResponse)
async def project_authorisation(request, slug: str):
    """What this caller may do here.

    The slug is a project's pid or its name — both name the same project. 404
    when the project does not exist; otherwise a caller that is not in it gets
    `role: null, admin: false, may_write: false`.
    """
    project = await _project_by_slug(slug)
    caller = caller_of(request)
    role = await effective_role(project, caller)
    return AuthzResponse(
        role=role,
        admin=caller.has_role("admin"),
        may_write=may_write(role),
    )


async def _project_by_slug(slug: str):
    """Resolve a project's pid or its name to the Project. 404 when absent."""
    try:
        return await project_repository.get(uuid.UUID(slug))
    except ValueError:
        pass
    try:
        return await project_repository.get_one(name=slug)
    except ObjectDoesNotExist:
        raise HttpError(404, "no such project")
