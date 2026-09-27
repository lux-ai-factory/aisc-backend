"""The test stamp (WP9): a new evaluation records the latest AI card version.

A pre_save receiver, so that Sean's evaluation route and model stay as they
are (amendment A1). It runs inside save(); the async route's asave() runs
save() in a worker thread, so the sync helper works in both. The version is
read on the database the evaluation is saved to (`using`), which is its
project's own (isolation I7.8).
"""
from django.db.models.signals import pre_save
from django.dispatch import receiver

from aisc_backend.models.evaluation import Evaluation
from aisc_backend.repositories.system_version_repository import latest_system_pid_sync


@receiver(pre_save, sender=Evaluation, dispatch_uid="aisc_evaluation_system_stamp")
def stamp_the_latest_system_version(sender, instance, raw=False, **kwargs):
    """A new evaluation records the card version that is the latest when it starts; it is never restamped."""
    if raw or not instance._state.adding or instance.system_id is not None or instance.project_id is None:
        return
    instance.system_id = latest_system_pid_sync(instance.project.platform_project_id,
                                                using=kwargs.get("using"))
