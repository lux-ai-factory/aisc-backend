"""
Scoped results + the analytics dashboard bridge (ANA-01), under /projects/{pid}/results.

Project-scoped like every other project router (a stranger gets 404, a member
reads). The run summary, the charts and the signed dashboard links are computed
by `services.results_dashboard` from the evaluation-owned ORM; the router only
checks the role, resolves the version and answers 404 for an unknown version.

The two write endpoints tell the dashboard about this project (the pilot's
`dashboard_bridge`): they need an editor, they are audited, and they are
best-effort — a dashboard that is down never stops a person from working.
"""
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.access import role_or_404
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.stats import ProjectResultsOut
from aisc_backend.services import dashboard_bridge, results_dashboard

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


@router.post("/{pid}/results/dashboard", response=dict)
async def register_on_dashboard(request, pid: uuid.UUID):
    """Tell the dashboard this project exists (make its datasets and dashboard).

    `"registered"` is the dashboard's answer; False means it did not take it —
    the bridge is closed (no URL or no token) or the dashboard is down — which is
    reported rather than raised: nothing in this product depends on the dashboard
    being up. The pilot pushes this on project create; here it is explicit, so a
    person can see the answer.
    """
    project = await _project_for(request, pid, "editor")
    # the bridge call is blocking HTTP: keep it off the event loop
    registered = await sync_to_async(dashboard_bridge.register, thread_sensitive=False)(project)
    await sync_to_async(log_action)(
        request,
        action="create",
        resource_type="dashboard_registration",
        resource_id=str(project.pid),
        metadata={"registered": registered},
    )
    return {
        "registered": registered,
        "bridge_configured": dashboard_bridge.configured(),
        "dashboard": {
            "url": results_dashboard.build_dashboard_url(project.pid),
        },
    }


@router.delete("/{pid}/results/dashboard", response=dict)
async def unregister_on_dashboard(request, pid: uuid.UUID):
    """The project is going: ask the dashboard to drop its objects."""
    project = await _project_for(request, pid, "editor")
    removed = await sync_to_async(dashboard_bridge.unregister, thread_sensitive=False)(project)
    await sync_to_async(log_action)(
        request,
        action="delete",
        resource_type="dashboard_registration",
        resource_id=str(project.pid),
        metadata={"removed": removed},
    )
    return {"removed": removed}
