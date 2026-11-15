import json
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
#: Celery raises SoftTimeLimitExceeded inside a task sent this signal: the worker's failure handler then ends the
#: plugin's processes and its run pass, as for a test that failed. SIGTERM would end the worker's child instead,
#: leaving the plugin (its own process group) running.
STOP_SIGNAL = "SIGUSR1"


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


#: Said when a run names a target or connection and the platform cannot say which endpoint it would reach.
ENDPOINTS_UNKNOWN = "the platform could not say which endpoint this run would reach; nothing was started"
#: The values of a resource input that name a target or a connection of the platform (as the worker reads them).
REF_PREFIXES = ("target:", "connection:")


def _plugin_of(plugin_config, using: str) -> dict:
    """One plugin of a run as its start records it: the package, its version, the saved configuration and the
    hash of that configuration as the ledger holds it (its engine.plugin.configured copy), and which settings
    the configuration gives the run, by key: a variable with the hash of its value, a secret by its key only."""
    from aisc_identity.canonical import sha256_of

    from aisc_backend.config_secrets import masked_for_ledger
    from aisc_backend.models import ProjectConfigCategory
    from aisc_backend.routers import plugin as plugin_routes

    plugin = plugin_config.plugin
    mappings = list(plugin_config.setting_mappings.using(using).select_related("project_config")
                    .order_by("plugin_config_key"))
    schema, ui = plugin_routes._plugin_form(plugin)
    held = masked_for_ledger(plugin_routes._ledger_config(plugin_config.config, schema, ui,
                                                          plugin_routes._kept_paths(mappings)))
    entry = {"name": plugin.name, "package": plugin.package_name, "version": plugin.version,
             "plugin_config_id": plugin_config.id, "config_sha256": sha256_of(held)}
    settings = []
    for mapping in mappings:
        setting = mapping.project_config
        given = {"key": mapping.plugin_config_key, "setting": setting.key, "category": setting.category}
        if setting.category != ProjectConfigCategory.SECRETS:
            given["value_sha256"] = sha256_of(setting.json_value)
        settings.append(given)
    if settings:
        entry["settings"] = settings
    return entry


def _contents_of(evaluation_uuid: uuid.UUID, using: str, platform_pid: str) -> tuple[str | None, dict]:
    """What a run is made of, for its start in the ledger: (the card version it assesses, the details). The
    details name each plugin (_plugin_of), the stored file and hash of each file input, the local model files,
    and the endpoint each target or connection it names would reach now. HttpError(503) when it names one and
    the platform cannot say which: nothing is started."""
    from aisc_backend import platform_helpers
    from aisc_backend.models import Evaluation, EvaluationInput, EvaluationPlugin

    evaluation = Evaluation.objects.using(using).get(pid=evaluation_uuid)
    tests = (EvaluationPlugin.objects.using(using).filter(evaluation=evaluation)
             .select_related("plugin_config__plugin").order_by("id"))
    details = {"plugins": [_plugin_of(test.plugin_config, using) for test in tests]}
    inputs = list(EvaluationInput.objects.using(using).filter(evaluation_plugin__evaluation=evaluation)
                  .select_related("component", "evaluation_plugin__plugin_config__plugin").order_by("id"))
    files = [{"plugin": put.evaluation_plugin.plugin_config.plugin.name, "input": put.name,
              "component": str(put.component.pid), "file": put.component.data, "sha256": put.component.data_sha256}
             for put in inputs if put.component is not None and put.component.is_file_backed and put.component.data]
    if files:
        details["inputs"] = files
    local_models = _local_models(evaluation_uuid, using)
    if local_models:
        details["local_models"] = local_models
    refs = list(dict.fromkeys(
        value for put in inputs if put.component is not None and put.component.component_type == "resource"
        and isinstance(value := (put.component.json_value or {}).get("value"), str) and value.startswith(REF_PREFIXES)))
    if refs:
        try:
            details["connections"] = platform_helpers.connection_fingerprints(platform_pid, refs)
        except platform_helpers.HelpersUnavailable as exc:
            raise HttpError(503, ENDPOINTS_UNKNOWN) from exc
    return (None if evaluation.system_id is None else str(evaluation.system_id)), details


@sync_to_async
def _recorded_and_sent(evaluation_uuid: uuid.UUID, send):
    """Send the run to the worker with its start in the project's ledger, in one transaction: the
    worker's events cite the start, so it is written before the worker can call back, and it is
    rolled back when the message cannot be sent. The evaluation is the run. `send` takes the request and the
    run the start cites ({"request_id", "run_id"}, or {} when no start was recorded), for the worker's own
    events to cite."""
    from aisc_backend.models import Evaluation, LedgerOutbox

    using = router.db_for_write(Evaluation)
    admitted = projectdb.admitted_pid.get()
    # read before the transaction: naming the endpoints asks the platform, and a refusal starts nothing
    contents = _contents_of(evaluation_uuid, using, admitted) if ledger.on() and admitted else None
    start = {}
    with transaction.atomic(using=using):
        if contents is not None:
            card_version, details = contents
            row = ledger.emit(ledger.RUN_REQUESTED, item_type="evaluation", item_id=evaluation_uuid,
                              run_id=evaluation_uuid, details=details, card_version=card_version, using=using)
            event = LedgerOutbox.objects.using(using).get(pk=row).event if row is not None else {}
            if event.get("request_id") and event.get("run_id"):
                start = {"request_id": event["request_id"], "run_id": event["run_id"]}
        return send(start)


async def run_evaluation(evaluation_uuid: uuid.UUID):
    """Dispatch a run: run_evaluation(evaluation_pid), as the upstream worker takes it, in both modes.

    Configurator: the run also travels in the
    Celery message headers, under aisc_run: the platform pid, the evaluation pid and the
    ticket that binds the run to both. The worker copies that header onto every task it
    publishes and sends the three back on every internal call; the door checks them. No DSN
    is sent: the door opens the project's database from the platform pid. When the run's start is in the
    ledger, aisc_run also carries the request and the run id the start cites (request_id, run_id): the worker
    sends them to the platform when it ends the run's pass, whose seal of the run's calls cites them.
    """
    if not deployment.is_configurator():
        return await _recorded_and_sent(evaluation_uuid,
                                        lambda _start: celery.send_task(RUN_EVAL_TASK, args=[evaluation_uuid]))
    platform_pid = await _platform_pid_of(evaluation_uuid)
    evaluation_pid = projectdb.normalise(evaluation_uuid)
    ticket = projectdb.run_ticket(platform_pid, evaluation_pid)
    return await _recorded_and_sent(evaluation_uuid, lambda start: celery.send_task(
        RUN_EVAL_TASK,
        args=[evaluation_uuid],
        headers={"aisc_run": {"project": platform_pid, "evaluation": evaluation_pid, "ticket": ticket, **start}},
    ))


@sync_to_async
def stop_run(evaluation_pid, dispatcher_task_id: str | None) -> None:
    """Revoke a run's tasks: its dispatcher (run_evaluation, in case it has not started) and every install and
    test the worker listed for the run (eval_tasks:<pid>). The one running ends; those queued never start."""
    tasks = [str(dispatcher_task_id)] if dispatcher_task_id else []
    listed = celery.backend.client.get(f"eval_tasks:{evaluation_pid}")
    if listed:
        tasks += [str(task) for task in json.loads(listed)]
    for task in tasks:
        celery.control.revoke(task, terminate=True, signal=STOP_SIGNAL)


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

