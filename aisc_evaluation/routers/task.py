import datetime
import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja.errors import HttpError

from aisc_backend.audit.log import log_action
from aisc_backend.auth.keycloak import require_role
from aisc_backend.services.monitoring import fetch_tasks
from aisc_evaluation.models import Evaluation
from aisc_evaluation.models import EvaluationStatus
from aisc_backend.services import celery_service

router = Router(tags=["task"])

STOP_MESSAGE = "Stopped by user"
RUNNING_PLUGIN_STATUSES = ["Pending", "Running"]


@router.get("/monitoring", response=dict, auth=require_role("admin"))
async def monitoring_tasks(request):
    return await fetch_tasks("tasks")


@router.get("/monitoring/{task_id}", response=dict, auth=require_role("admin"))
async def monitoring_task(request, task_id: uuid.UUID):
    return await fetch_tasks(f"task/info/{task_id}")


@router.get("{pid}/status", response=dict)
async def get_evaluation_tasks_status(request, pid: uuid.UUID):
    result = await celery_service.get_evaluation_tasks_status(pid)
    return result


@router.post("{pid}/stop", response=dict)
async def stop_evaluation(request, pid: uuid.UUID):
    """Stop a whole evaluation: pid is the evaluation's dispatched celery task id."""
    evaluation = await Evaluation.objects.select_related("project").filter(task=pid).afirst()
    if evaluation is None:
        raise HttpError(404, "No evaluation found")

    stopped = await celery_service.stop_evaluation(evaluation.pid, pid)

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    # remember which running plugins get stopped before marking them failed
    stopped_plugin_pids = [
        str(v)
        async for v in evaluation.evaluation_plugins
        .filter(status__in=RUNNING_PLUGIN_STATUSES)
        .values_list("pid", flat=True)
    ]
    evaluation.status = EvaluationStatus.Failed
    await evaluation.asave()
    await evaluation.evaluation_plugins.filter(status__in=RUNNING_PLUGIN_STATUSES).aupdate(
        status="Failed", error_message=STOP_MESSAGE, finished_at=now
    )

    await sync_to_async(log_action)(
        request, action="stop", resource_type="evaluation", resource_id=str(evaluation.pid),
        metadata={
            "projectPid": str(evaluation.project.pid),
            "status": "Failed",
            "pluginsStopped": stopped_plugin_pids,
            "stoppedTaskIds": stopped,
        })
    return {"stopped": stopped}
