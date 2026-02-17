import uuid

from celery import Celery
from celery.result import AsyncResult, GroupResult
from celery.states import SUCCESS

from a4s_plugin_interface import TaskProgress
from config.settings import CELERY_BROKER_URL, REDIS_BACKEND_URL, CELERY_APP_NAME

celery: Celery = Celery(
    CELERY_APP_NAME, broker=CELERY_BROKER_URL, backend=REDIS_BACKEND_URL
)

RUN_EVAL_TASK = "a4s_eval.celery_tasks.run_evaluation"
RUN_PLUGIN_TASK = "a4s_eval.celery_tasks.run_plugin"


async def run_evaluation(evaluation_uuid: uuid.UUID):
    run_evaluation_task_result = celery.send_task(RUN_EVAL_TASK, args=[evaluation_uuid])
    return run_evaluation_task_result


async def get_evaluation_tasks_status(task_pid: uuid.UUID) -> dict[str, TaskProgress]:
    evaluation_task = AsyncResult(str(task_pid), app=celery)

    # evaluation task spawns subtasks for each plugin and should complete immediately
    if evaluation_task.state == SUCCESS and isinstance(evaluation_task.result, dict) and 'evaluation_pid' in evaluation_task.result:

        #args[0] is the plugin name
        plugin_statuses = {
            child.parent.args[0]: child.parent.info
            for group in evaluation_task.children or []
            for child in group.children or []
            if child.parent.name == RUN_PLUGIN_TASK
        }

        return plugin_statuses

    return {}


async def autodiscover_datashape(datashape_pid: uuid.UUID):
    result = celery.send_task("a4s_eval.tasks.datashape_tasks.auto_discover_datashape", args=[datashape_pid])
    return result


