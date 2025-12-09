import uuid

from celery import Celery
from celery.result import AsyncResult, GroupResult

from config.settings import CELERY_BROKER_URL, REDIS_BACKEND_URL, CELERY_APP_NAME

celery: Celery = Celery(
    CELERY_APP_NAME, broker=CELERY_BROKER_URL, backend=REDIS_BACKEND_URL
)

async def trigger_evaluation_task():
    result = celery.send_task("a4s_eval.celery_tasks.poll_and_run_evaluation")
    return result


async def run_evaluation_task(evaluation_pid: uuid.UUID):
    run_evaluation_task_result = celery.send_task("a4s_eval.celery_tasks.run_evaluation", args=[evaluation_pid])
    return run_evaluation_task_result


async def run_plugins(project_pid: uuid.UUID):
    run_plugin_task = celery.send_task("a4s_eval.celery_tasks.run_plugins", args=[project_pid])
    return run_plugin_task


async def check_task_status(task_pid: uuid.UUID):
    res = AsyncResult(str(task_pid), app=celery)

    if res.state == 'SUCCESS' and isinstance(res.result, dict) and 'group_id' in res.result:
        group_id = res.result['group_id']
        group_res = GroupResult.restore(group_id, app=celery)

        return {
            "state": "DONE" if group_res.ready() else "RUNNING",
            "group_id": group_id
        }

    return {
        "state": res.state,
        "id": task_pid
    }


async def autodiscover_datashape(datashape_pid: uuid.UUID):
    result = celery.send_task("a4s_eval.tasks.datashape_tasks.auto_discover_datashape", args=[datashape_pid])
    return result


