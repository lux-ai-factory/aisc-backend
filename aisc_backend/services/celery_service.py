import json
import uuid
import logging

from celery import Celery
from celery.result import AsyncResult
from celery.states import SUCCESS

from aisc_plugin_interface import TaskProgress
from config.settings import CELERY_BROKER_URL, REDIS_BACKEND_URL, CELERY_APP_NAME

logger = logging.getLogger(__name__)

celery: Celery = Celery(
    CELERY_APP_NAME, broker=CELERY_BROKER_URL, backend=REDIS_BACKEND_URL
)

RUN_EVAL_TASK = "aisc_eval.celery_tasks.run_evaluation"
RUN_PLUGIN_TASK = "aisc_eval.celery_tasks.run_plugin"


async def run_evaluation(evaluation_uuid: uuid.UUID):
    run_evaluation_task_result = celery.send_task(RUN_EVAL_TASK, args=[evaluation_uuid])
    return run_evaluation_task_result


async def get_evaluation_tasks_status(task_pid: uuid.UUID) -> dict[str, TaskProgress]:
    evaluation_task = AsyncResult(str(task_pid), app=celery)

    # evaluation task spawns subtasks for each plugin and should complete immediately
    if (
        evaluation_task.state == SUCCESS
        and isinstance(evaluation_task.result, dict)
        and "evaluation_pid" in evaluation_task.result
    ):
        plugin_task_ids = evaluation_task.result.get("plugin_task_ids", [])

        plugin_statuses = {}
        for task_id in plugin_task_ids:
            task_result = AsyncResult(task_id, app=celery)
            if (
                task_result.state == "RUNNING"
                and isinstance(task_result.info, dict)
                and "plugin_name" in task_result.info
            ):
                plugin_statuses[task_result.info["plugin_name"]] = task_result.info

        return plugin_statuses

    return {}


async def get_evaluation_task_state(task_pid: uuid.UUID) -> str:
    """The celery state of one evaluation's run task (a stable string, plus
    'NO_TASK' when the evaluation was never dispatched)."""
    if task_pid is None:
        return "NO_TASK"
    try:
        result = AsyncResult(str(task_pid), app=celery)
        return str(result.state)
    except Exception:  # noqa: BLE001 - a broker hiccup must not fail the listing
        return "UNKNOWN"


def _revoke_task(task_id: str) -> None:
    try:
        celery.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception:  # noqa: BLE001 - a broker hiccup must not fail the stop
        logger.warning("revoke failed for task %s", task_id)


async def stop_evaluation(evaluation_pid: uuid.UUID, task_pid: uuid.UUID) -> list[str]:
    """Revoke every celery task of one evaluation run (the eval task plus all
    plugin subtasks), so nothing keeps running after the user stops it."""
    ids = [str(task_pid)]
    try:
        raw = celery.backend.client.get(f"eval_tasks:{evaluation_pid}")
        if raw:
            ids += list(json.loads(raw))
    except Exception:  # noqa: BLE001 - a broker hiccup must not fail the stop
        logger.warning("could not read plugin task ids for evaluation %s", evaluation_pid)
    for task_id in ids:
        _revoke_task(task_id)
    return ids

