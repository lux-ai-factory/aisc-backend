import uuid

from asgiref.sync import sync_to_async
from celery import Celery
from celery.result import AsyncResult
from celery.states import SUCCESS
from django.db import router, transaction
from ninja.errors import HttpError

from aisc_plugin_interface import TaskProgress
from aisc_backend import deployment, ledger, projectdb
from config.settings import CELERY_BROKER_URL, REDIS_BACKEND_URL, CELERY_APP_NAME

celery: Celery = Celery(
    CELERY_APP_NAME, broker=CELERY_BROKER_URL, backend=REDIS_BACKEND_URL
)

RUN_EVAL_TASK = "aisc_eval.celery_tasks.run_evaluation"


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


def _local_models(evaluation_uuid: uuid.UUID, using: str) -> list[dict]:
    """The inputs the engine marked local when it created the run: the plugin, the input and the
    file's name (its hash comes with the worker's marker, engine.evaluation.input_marked_local)."""
    from aisc_backend.models import EvaluationInput

    inputs = (EvaluationInput.objects.using(using).filter(evaluation_plugin__evaluation__pid=evaluation_uuid)
              .order_by("id").values_list("evaluation_plugin__plugin_config__plugin__name", "name", "value"))
    return [{"plugin": plugin, "input": name, "file": value["local_model"].get("file")}
            for plugin, name, value in inputs
            if isinstance(value, dict) and isinstance(value.get("local_model"), dict)]


@sync_to_async
def _recorded_and_sent(evaluation_uuid: uuid.UUID, send):
    """Send the run to the worker with its start in the project's ledger, in one transaction: the
    worker's events cite the start, so it is written before the worker can call back, and it is
    rolled back when the message cannot be sent. The evaluation is the run."""
    from aisc_backend.models import Evaluation, EvaluationPlugin

    using = router.db_for_write(Evaluation)
    with transaction.atomic(using=using):
        if ledger.on():
            plugins = list(EvaluationPlugin.objects.using(using).filter(evaluation__pid=evaluation_uuid)
                           .order_by("id").values_list("plugin_config__plugin__name", flat=True))
            details = {"plugins": plugins}
            local_models = _local_models(evaluation_uuid, using)
            if local_models:
                details["local_models"] = local_models
            ledger.emit(ledger.RUN_REQUESTED, item_type="evaluation", item_id=evaluation_uuid,
                        run_id=evaluation_uuid, details=details, using=using)
        return send()


async def run_evaluation(evaluation_uuid: uuid.UUID):
    """Dispatch a run: run_evaluation(evaluation_pid), as the upstream worker takes it, in both modes.

    Configurator: the run also travels in the
    Celery message headers, under aisc_run: the platform pid, the evaluation pid and the
    ticket that binds the run to both. The worker copies that header onto every task it
    publishes and sends the three back on every internal call; the door checks them. No DSN
    is sent: the door opens the project's database from the platform pid.
    """
    if not deployment.is_configurator():
        return await _recorded_and_sent(evaluation_uuid,
                                        lambda: celery.send_task(RUN_EVAL_TASK, args=[evaluation_uuid]))
    platform_pid = await _platform_pid_of(evaluation_uuid)
    evaluation_pid = projectdb.normalise(evaluation_uuid)
    ticket = projectdb.run_ticket(platform_pid, evaluation_pid)
    return await _recorded_and_sent(evaluation_uuid, lambda: celery.send_task(
        RUN_EVAL_TASK,
        args=[evaluation_uuid],
        headers={"aisc_run": {"project": platform_pid, "evaluation": evaluation_pid, "ticket": ticket}},
    ))


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

