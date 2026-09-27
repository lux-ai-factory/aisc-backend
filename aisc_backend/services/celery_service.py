import uuid

from celery import Celery
from celery.result import AsyncResult
from celery.states import SUCCESS
from ninja.errors import HttpError

from aisc_plugin_interface import TaskProgress
from aisc_backend import projectdb
from config.settings import CELERY_BROKER_URL, REDIS_BACKEND_URL, CELERY_APP_NAME

celery: Celery = Celery(
    CELERY_APP_NAME, broker=CELERY_BROKER_URL, backend=REDIS_BACKEND_URL
)

RUN_EVAL_TASK = "aisc_eval.celery_tasks.run_evaluation"
RUN_PLUGIN_TASK = "aisc_eval.celery_tasks.run_plugin"


async def _platform_pid_of(evaluation_uuid: uuid.UUID) -> str:
    """The platform project this run belongs to: the one the door admitted, or
    (outside a request, one database) the platform link of its engine project."""
    admitted = projectdb.admitted_pid.get()
    if admitted:
        return projectdb.normalise(admitted)
    from aisc_backend.models.evaluation import Evaluation

    evaluation = await Evaluation.objects.select_related("project").aget(pid=evaluation_uuid)
    linked = evaluation.project.platform_project_id if evaluation.project else None
    if linked is None:
        raise HttpError(409, "this evaluation belongs to no platform project, so no run can be started for it")
    return projectdb.normalise(linked)


async def run_evaluation(evaluation_uuid: uuid.UUID):
    """Dispatch a run (I7.3): run_evaluation(platform_pid, evaluation_pid, ticket).

    The ticket binds the run to its project and its evaluation; the worker sends
    it back on every internal call and the door checks it. No DSN is sent: the
    door opens the project's database from the platform pid.
    """
    platform_pid = await _platform_pid_of(evaluation_uuid)
    evaluation_pid = projectdb.normalise(evaluation_uuid)
    ticket = projectdb.run_ticket(platform_pid, evaluation_pid)
    run_evaluation_task_result = celery.send_task(RUN_EVAL_TASK, args=[platform_pid, evaluation_pid, ticket])
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

