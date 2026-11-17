"""
Scoped results + the analytics dashboard bridge (ANA-01), under /projects/{pid}/results.

Project-scoped like every other project router (a stranger gets 404, a member
reads). The run summary and the dashboard links are computed by
`services.results_dashboard` from the evaluation-owned ORM; the router only
checks the role, resolves the version and answers 404 for an unknown version.
"""
import uuid

from ninja import Router
from ninja.errors import HttpError

from aisc_backend.auth.access import role_or_404
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.stats import ProjectResultsOut
from aisc_backend.services import results_dashboard

router = Router(tags=["results"])

project_repository = ProjectRepository()


@router.get("/{pid}/results", response=ProjectResultsOut)
async def get_project_results(
    request, pid: uuid.UUID, version: str | None = None, target: str | None = None
):
    """One AI-card version's scoped run summary and the dashboard bridge.

    `version` is an AI-card version pid (latest when omitted); `target` an
    AI-component pid to scope the runs to. The external dashboard links are
    present only when `RESULTS_DASHBOARD_URL` is configured.
    """
    project = await _project_for(request, pid)
    try:
        return await results_dashboard.scoped_results(project, version, target)
    except LookupError as exc:
        raise HttpError(404, str(exc))


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project
