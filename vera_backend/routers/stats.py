import uuid

from ninja import Router

from vera_backend.repositories.stats_repository import StatsRepository
from vera_backend.schemas.stats import (
    ProjectStatsOverview,
    ProjectStatsMetricBreakdown,
    ProjectStatsPluginUsage,
    ProjectStatsPluginDurations,
)

router = Router(tags=["stats"])

stats_repository = StatsRepository()


@router.get("/projects/{pid}/overview", response=ProjectStatsOverview)
async def get_project_stats_overview(request, pid: uuid.UUID):
    """High-level stats summary for a project."""
    return await stats_repository.get_overview(pid)


@router.get("/projects/{pid}/metrics", response=ProjectStatsMetricBreakdown)
async def get_project_metric_breakdown(request, pid: uuid.UUID):
    """Per-metric score statistics for a project."""
    metrics = await stats_repository.get_metric_breakdown(pid)
    return {"metrics": metrics}


@router.get("/projects/{pid}/plugins", response=ProjectStatsPluginUsage)
async def get_project_plugin_usage(request, pid: uuid.UUID):
    """Per-plugin usage summary for a project."""
    plugins = await stats_repository.get_plugin_usage(pid)
    return {"plugins": plugins}


@router.get("/projects/{pid}/plugin-durations", response=ProjectStatsPluginDurations)
async def get_project_plugin_durations(request, pid: uuid.UUID):
    """Per-run duration history for all plugins in a project."""
    runs = await stats_repository.get_plugin_durations(pid)
    return {"runs": runs}
