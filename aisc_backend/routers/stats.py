from asgiref.sync import sync_to_async
import uuid

from aisc_backend.auth import membership
from aisc_backend.auth.keycloak import KeycloakAuth
from ninja import Router

from aisc_backend.repositories.stats_repository import StatsRepository
from aisc_backend.schemas.stats import (
    ProjectStatsOverview,
    ProjectStatsMetricBreakdown,
    ProjectStatsPluginUsage,
    ProjectStatsPluginDurations,
)

# Named here as well as API-wide, so request.auth is the verified claims
# for this router on its own: that is what the membership check reads.
router = Router(tags=["stats"], auth=KeycloakAuth())

stats_repository = StatsRepository()


@router.get("/projects/{pid}/overview", response=ProjectStatsOverview)
async def get_project_stats_overview(request, pid: uuid.UUID):
    """High-level stats summary for a project."""
    await sync_to_async(membership.for_project_pid)(request, pid)
    return await stats_repository.get_overview(pid)


@router.get("/projects/{pid}/metrics", response=ProjectStatsMetricBreakdown)
async def get_project_metric_breakdown(request, pid: uuid.UUID):
    """Per-metric score statistics for a project."""
    await sync_to_async(membership.for_project_pid)(request, pid)
    metrics = await stats_repository.get_metric_breakdown(pid)
    return {"metrics": metrics}


@router.get("/projects/{pid}/plugins", response=ProjectStatsPluginUsage)
async def get_project_plugin_usage(request, pid: uuid.UUID):
    """Per-plugin usage summary for a project."""
    await sync_to_async(membership.for_project_pid)(request, pid)
    plugins = await stats_repository.get_plugin_usage(pid)
    return {"plugins": plugins}


@router.get("/projects/{pid}/plugin-durations", response=ProjectStatsPluginDurations)
async def get_project_plugin_durations(request, pid: uuid.UUID):
    """Per-run duration history for all plugins in a project."""
    await sync_to_async(membership.for_project_pid)(request, pid)
    runs = await stats_repository.get_plugin_durations(pid)
    return {"runs": runs}
