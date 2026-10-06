"""The engine's row for a platform project (configurator only; config/urls.py mounts it there).

Moved verbatim from routers/project.py (adapt plan 2026-09-28, item 2), so Sean's project
router stays his. Standalone has no platform, so this router is not mounted there (404).
"""
import uuid

from asgiref.sync import sync_to_async
from django.db import IntegrityError
from ninja import Router

from aisc_backend.audit.log import log_action
from aisc_backend.auth import membership
from aisc_backend.platform_projects import platform_project_name
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.project import ProjectOutSchema

router = Router(tags=["project"])

project_repository = ProjectRepository()


@router.post("/{platform_project_id}", response=ProjectOutSchema)
async def project_for_platform(request, platform_project_id: uuid.UUID):
    """This engine's row for a platform project, making it the first time (configurator only).

    There is no workspace to create here. The project is chosen once, on the
    launcher; opening the engine inside it means working on it, so the first
    visit makes the row, every visit after finds it, and nobody is asked to
    name anything. The name shown is the platform's own.
    """
    # Entering the engine inside a project is work on that project, so it takes
    # an editor. It is not a way into one: a stranger is told there is no such
    # project, the same as everywhere else.
    await sync_to_async(membership.require)(request, platform_project_id, "editor")

    existing = await project_repository.filter(platform_project_id=platform_project_id)
    if existing:
        return existing[0]

    name = await sync_to_async(platform_project_name)(platform_project_id)
    try:
        project = await project_repository.create(
            name or f"project-{str(platform_project_id)[:8]}", platform_project_id
        )
    except IntegrityError:
        # Two first visits at once (the home page and the install dialog both call this): the other one
        # made the row between the lookup and the create (one_project_per_platform_project). Its row is
        # this one's answer.
        made = await project_repository.filter(platform_project_id=platform_project_id)
        if not made:
            raise
        return made[0]
    await sync_to_async(log_action)(
        request, action="create", resource_type="project",
        resource_id=str(project.pid),
        metadata={"name": project.name, "platformProjectPid": str(platform_project_id)})
    return project
