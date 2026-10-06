"""Every evaluation names its target (targets plan v2, 2026-09-29; configurator only).

The evaluation form reads a plugin's input definitions from GET /plugins/{pid}/input_definitions
(routers/plugin.py, frozen). config/urls.py mounts this router in front of it in configurator
mode: the same answer, plus one required `resource` input, `target`: the system, or the component
of its AI card, that the evaluation is about. The platform keeps the targets and mirrors each as a
component of this project whose value is `target:<platform pid>/<key>`. The form's own check asks
every run for it, and the frozen create route stores the pick as an ordinary EvaluationInput: with
the card version stamp on the evaluation, every result says what it assessed. A plugin that
declares `target` itself (the plugin interface's system_under_test) keeps its own.

The system target is made sure of here, so there is always one to pick, even before anybody has
opened the platform's targets page: the platform finds it again by its value and renames it.
"""
import logging
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from ninja import Router

from aisc_backend import deployment
from aisc_backend.routers.plugin import get_plugin_input_definitions as frozen_input_definitions
from aisc_plugin_interface import InputDefinition, InputType

logger = logging.getLogger(__name__)

router = Router(tags=["plugin"])

TARGET = "target"
TARGET_LABEL = "Target of the assessment (the system, or one of its components)"


def system_reference(platform_pid) -> str:
    return f"target:{platform_pid}/system"


def _configurator() -> bool:
    return getattr(settings, "AISC_DEPLOYMENT", None) == deployment.CONFIGURATOR


def _system_target_exists(system, value) -> bool:
    from aisc_backend.models import AIComponent

    return AIComponent.objects.filter(system=system, json_value__value=value).exists()


@sync_to_async
def _ensure_system_target() -> None:
    from django.db import IntegrityError, transaction

    from aisc_backend.models import AIComponent, AISystem, Project

    project = Project.objects.filter(platform_project_id__isnull=False).first()
    if project is None:
        return
    system = AISystem.objects.filter(project=project).first()
    if system is None:
        return
    value = system_reference(project.platform_project_id)
    if _system_target_exists(system, value):
        return
    # two forms loaded at once both find none: the unique index (migration 0022) refuses the second,
    # which then has the one the first made (code review 2026-10-06)
    try:
        with transaction.atomic():
            AIComponent.objects.create(system=system, name=f"Target · System: {project.name}",
                                       component_type="resource", json_value={"value": value})
    except IntegrityError:
        pass


async def ensure_system_target() -> None:
    await _ensure_system_target()


@router.get("/{plugin_pid}/input_definitions", response=list[InputDefinition])
async def input_definitions_with_target(request, plugin_pid: uuid.UUID):
    definitions = list(await frozen_input_definitions(request, plugin_pid))
    if not _configurator():
        return definitions
    if any(d.name == TARGET for d in definitions):
        # a plugin's own target (declared optional, for standalone) is required here (O2)
        return [d.model_copy(update={"required": True}) if d.name == TARGET else d for d in definitions]
    try:
        await ensure_system_target()
    except Exception:  # the definitions matter more than the mirror; the platform makes it too
        logger.exception("the system target of this project could not be made sure of")
    return [*definitions,
            InputDefinition(name=TARGET, label=TARGET_LABEL, input_type=InputType.RESOURCE, required=True)]
