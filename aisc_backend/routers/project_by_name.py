"""A project by any name, "/" included (code review 2026-10-06).

The engine names its project after the platform project, whose name is free text. Sean's
GET /projects/by-name/{name} (routers/project.py, frozen) takes one path segment, and the server decodes %2F
before routing, so a name with "/" could not be found. This takes the name as a query parameter, where every
character travels: GET /projects/by-name?name=... config/urls.py mounts it in front of /projects, so its path
is matched before Sean's /projects/{pid} would take "by-name" for a pid; his route stays.
"""
from ninja import Router

from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.routers.project import ProjectOutSchema

router = Router(tags=["project"])
project_repository = ProjectRepository()


@router.get("", response=ProjectOutSchema)
async def get_project_by_name_query(request, name: str):
    return await project_repository.get_one(name=name)
