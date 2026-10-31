"""The ledger events of the engine's own rows: the project, its components, its settings and secrets,
and the status of its evaluations.

Receivers rather than calls in the routes, so that the upstream project, component and settings routes stay
as they are (as system_stamp.py does for the evaluation). Each receiver runs inside the write it
reports: the repositories save in a transaction (repositories/base_repository.py) and Django deletes
in one, so the row and its event commit together. The pre_save receiver keeps what the row was, to
name the fields a save changed; an event names fields and keys, never a setting's or a secret's value.
An evaluation's status is changed by the worker, so its events cite the run (ledger.emit_for_run).
"""
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from aisc_backend import ledger, projectdb
from aisc_backend.models import (AIComponent, Evaluation, EvaluationPlugin, Project, ProjectConfig,
                                 ProjectConfigCategory)

#: The fields of each row an event may say changed, and the name it says.
_FIELDS = {
    Project: {"name": "name", "description": "description"},
    AIComponent: {"name": "name", "description": "description", "json_value": "json_value",
                  "source_dataset_id": "source_dataset", "data": "data"},
    ProjectConfig: {"name": "name", "encrypted_value": "value", "json_value": "json_value"},
    Evaluation: {"status": "status"},
    EvaluationPlugin: {"status": "status"},
}


def _recording(raw=False) -> bool:
    return not raw and ledger.on() and projectdb.admitted_pid.get() is not None


def _changed(instance) -> list[str]:
    before = getattr(instance, "_ledger_before", None) or {}
    return [name for field, name in _FIELDS[type(instance)].items()
            if field in before and before[field] != getattr(instance, field)]


def _target_mirror(component) -> bool:
    """The platform's copy of a target (target_input.py): the platform records its targets itself."""
    return str((component.json_value or {}).get("value", "")).startswith("target:")


def _kind(config) -> str:
    return "secret" if config.category == ProjectConfigCategory.SECRETS else "setting"


@receiver(pre_save, dispatch_uid="aisc_ledger_before")
def keep_what_the_row_was(sender, instance, raw=False, using=None, update_fields=None, **kwargs):
    if sender not in _FIELDS or instance._state.adding or instance.pk is None or not _recording(raw):
        return
    # a save of named fields changes only those: the others' stale values on the instance are not a change
    fields = [field for field in _FIELDS[sender] if update_fields is None or field in update_fields]
    instance._ledger_before = (sender.objects.using(using).filter(pk=instance.pk).values(*fields).first()
                               if fields else None)


@receiver(post_save, sender=Project, dispatch_uid="aisc_ledger_project")
def project_saved(sender, instance, created, raw=False, using=None, **kwargs):
    if not _recording(raw):
        return
    if created:
        ledger.emit("engine.project.created", item_type="engine_project", item_id=instance.pid, using=using)
    else:
        ledger.emit("engine.project.updated", item_type="engine_project", item_id=instance.pid,
                    details={"changed": _changed(instance)}, using=using)


@receiver(post_save, sender=AIComponent, dispatch_uid="aisc_ledger_component")
def component_saved(sender, instance, created, raw=False, using=None, **kwargs):
    if not _recording(raw) or _target_mirror(instance):
        return
    if created:
        ledger.emit("engine.component.created", item_type="component", item_id=instance.pid,
                    details={"component_type": instance.component_type}, using=using)
        return
    changed = _changed(instance)
    if "data" in changed:                                             # a new file: the upstream PUT .../data
        ledger.emit("engine.component.file_uploaded", item_type="component", item_id=instance.pid,
                    details={"size": instance.file_size}, using=using)
    else:
        ledger.emit("engine.component.updated", item_type="component", item_id=instance.pid,
                    details={"changed": changed}, using=using)


@receiver(post_delete, sender=AIComponent, dispatch_uid="aisc_ledger_component_deleted")
def component_deleted(sender, instance, using=None, **kwargs):
    if not _recording() or _target_mirror(instance):
        return
    ledger.emit("engine.component.deleted", item_type="component", item_id=instance.pid,
                details={"component_type": instance.component_type}, using=using)


@receiver(post_save, sender=ProjectConfig, dispatch_uid="aisc_ledger_project_config")
def project_config_saved(sender, instance, created, raw=False, using=None, **kwargs):
    if not _recording(raw):
        return
    kind = _kind(instance)
    if created:
        ledger.emit(f"engine.{kind}.created", item_type=kind, item_id=instance.pid,
                    details={"key": instance.key}, using=using)
    else:
        ledger.emit(f"engine.{kind}.updated", item_type=kind, item_id=instance.pid,
                    details={"key": instance.key, "changed": _changed(instance)}, using=using)


@receiver(post_delete, sender=ProjectConfig, dispatch_uid="aisc_ledger_project_config_deleted")
def project_config_deleted(sender, instance, using=None, **kwargs):
    if not _recording():
        return
    kind = _kind(instance)
    ledger.emit(f"engine.{kind}.deleted", item_type=kind, item_id=instance.pid,
                details={"key": instance.key}, using=using)


def _status_change(instance) -> dict | None:
    before = getattr(instance, "_ledger_before", None) or {}
    if "status" not in _changed(instance):
        return None
    return {"status_before": str(before["status"]), "status_after": str(instance.status)}


@receiver(post_save, sender=Evaluation, dispatch_uid="aisc_ledger_evaluation_status")
def evaluation_saved(sender, instance, created, raw=False, using=None, **kwargs):
    if created or not _recording(raw):
        return
    change = _status_change(instance)
    if change:
        ledger.emit_for_run("engine.evaluation.status_changed", instance.pid, item_type="evaluation",
                            item_id=instance.pid, details=change, using=using)


@receiver(post_save, sender=EvaluationPlugin, dispatch_uid="aisc_ledger_evaluation_plugin_status")
def evaluation_plugin_saved(sender, instance, created, raw=False, using=None, **kwargs):
    if created or not _recording(raw):
        return
    change = _status_change(instance)
    if not change:
        return
    row = (EvaluationPlugin.objects.using(using).filter(pk=instance.pk)
           .values("evaluation__pid", "plugin_config__plugin__name").first())
    if row is None:
        return
    evaluation = row["evaluation__pid"]
    ledger.emit_for_run("engine.evaluation.plugin_status_changed", evaluation, item_type="evaluation",
                        item_id=evaluation, details={"plugin": row["plugin_config__plugin__name"], **change},
                        using=using)
