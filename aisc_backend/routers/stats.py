import uuid

from ninja import Router

from aisc_backend.auth.access import role_or_404
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.repositories.stats_repository import StatsRepository
from aisc_backend.schemas.stats import (
    ProjectStatsOverview,
    ProjectStatsMetricBreakdown,
    ProjectStatsPluginUsage,
    ProjectStatsPluginDurations,
)

router = Router(tags=["stats"])

stats_repository = StatsRepository()
project_repository = ProjectRepository()


async def _project_for(request, pid: uuid.UUID, needed: str = "viewer") -> Project:
    """Resolve the project then check the caller's role, so a stranger gets 404.

    These endpoints read one project's aggregates; without this check a
    non-member could read any project's numbers by pid.
    """
    project = await project_repository.get(pid)
    await role_or_404(request, project, needed)
    return project


@router.get("/projects/{pid}/overview", response=ProjectStatsOverview)
async def get_project_stats_overview(request, pid: uuid.UUID):
    """High-level stats summary for a project."""
    await _project_for(request, pid)
    return await stats_repository.get_overview(pid)


@router.get("/projects/{pid}/metrics", response=ProjectStatsMetricBreakdown)
async def get_project_metric_breakdown(request, pid: uuid.UUID):
    """Per-metric score statistics for a project."""
    await _project_for(request, pid)
    metrics = await stats_repository.get_metric_breakdown(pid)
    return {"metrics": metrics}


@router.get("/projects/{pid}/plugins", response=ProjectStatsPluginUsage)
async def get_project_plugin_usage(request, pid: uuid.UUID):
    """Per-plugin usage summary for a project."""
    await _project_for(request, pid)
    plugins = await stats_repository.get_plugin_usage(pid)
    return {"plugins": plugins}


@router.get("/projects/{pid}/plugin-durations", response=ProjectStatsPluginDurations)
async def get_project_plugin_durations(request, pid: uuid.UUID):
    """Per-run duration history for all plugins in a project."""
    await _project_for(request, pid)
    runs = await stats_repository.get_plugin_durations(pid)
    return {"runs": runs}
