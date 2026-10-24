"""Every evaluation names its target (configurator only).

The evaluation form reads a plugin's input definitions from GET /plugins/{pid}/input_definitions
(routers/plugin.py, upstream's). config/urls.py mounts this router in front of it in configurator
mode: the same inputs, arranged so that every evaluation names what it assesses. An AI card target
is the `resource` input `target`: the system, or the component of its AI card, that the evaluation
is about. The platform keeps the targets and mirrors each as a component of this project whose value
is `target:<platform pid>/<key>`. The form's own check asks every run for the inputs marked required,
and upstream's create route stores each pick as an ordinary EvaluationInput: with the card version
stamp on the evaluation, every result says what it assessed. A plugin that assesses only a local
model file is given no `target`; one that declares `target` itself keeps its own.

A plugin that declares what it assesses (the plugin interface's @assesses, or one of the older
declarations alone, read as an AI card target) is asked for it first, and only for the kinds it
declares: the form lists, for each input, the project's components of that input's type, so an AI card
target is a `resource` input (the platform's mirrors) and a local model file is the plugin's own
`model` input (files uploaded in Settings); the other kind cannot be picked. Then come the plugin's
other inputs, then its helper models (`helper:<role>`), labelled apart: they are never what is
assessed. A plugin that declares nothing keeps the form as it was.

The system target is made sure of here, so there is one to pick even before anybody has opened the
platform's targets page: the platform finds it again by its value and renames it. Not before the
project has an AI card version, though: a project used only for the engine has no AI card targets.
"""
import logging
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from ninja import Router

from aisc_backend import deployment
from aisc_backend.repositories.system_version_repository import latest_system_pid_sync
from aisc_backend.routers import plugin as plugin_routes
from aisc_backend.routers.plugin import get_plugin_input_definitions as upstream_input_definitions
from aisc_plugin_interface import InputDefinition, InputType, helper_roles_of, target_kinds_of
from aisc_plugin_interface.assesses import card_needs_connection, card_protocols_of
from aisc_plugin_interface.helpers import HELPER_INPUT_PREFIX, helper_input_name

logger = logging.getLogger(__name__)

router = Router(tags=["plugin"])

TARGET = "target"
TARGET_LABEL = "Target of the assessment (the system, or one of its components)"
CARD, LOCAL_MODEL = "card", "local_model"
#: (AI card target, local model file) labels, by what the plugin assesses
ASSESSED_LABELS = {
    (CARD,): ("What is assessed (AI card)", None),
    (LOCAL_MODEL,): (None, "What is assessed (local model file)"),
    (CARD, LOCAL_MODEL): ("What is assessed: from the AI card", "What is assessed: or a local model file"),
}


def system_reference(platform_pid) -> str:
    return f"target:{platform_pid}/system"


def _configurator() -> bool:
    return getattr(settings, "AISC_DEPLOYMENT", None) == deployment.CONFIGURATOR


def _system_target_exists(system, value) -> bool:
    from aisc_backend.models import AIComponent

    return AIComponent.objects.filter(system=system, json_value__value=value).exists()


def system_target_offered(project) -> bool:
    """Whether the project's system is a target: once the project has an AI card version (the one each
    evaluation is stamped with, signals/system_stamp.py)."""
    return latest_system_pid_sync(project.platform_project_id, using=project._state.db) is not None


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
    if _system_target_exists(system, value) or not system_target_offered(project):
        return
    # two forms loaded at once both find none: the unique index (migration 0022) refuses the second,
    # which then has the one the first made
    try:
        with transaction.atomic():
            AIComponent.objects.create(system=system, name=f"AI card · System: {project.name} · not connected",
                                       component_type="resource", json_value={"value": value})
    except IntegrityError:
        pass


async def ensure_system_target() -> None:
    await _ensure_system_target()


async def loaded_plugin(plugin_pid: uuid.UUID):
    plugin = await plugin_routes.plugin_repository.get(plugin_pid)
    return plugin_routes.plugin_loader.load_plugin(plugin.package_name, plugin.name, plugin.version)


def _helper_label(role: str) -> str:
    return f"Helper model · {role.capitalize()} (not assessed)"


def form_inputs(definitions: list[InputDefinition], plugin) -> list[InputDefinition]:
    """What is assessed (only the kinds the plugin declares), the plugin's other inputs, its helper models."""
    kinds = target_kinds_of(plugin)
    card_label, local_label = ASSESSED_LABELS[kinds]
    required = len(kinds) == 1  # with both kinds a run assesses one or the other
    by_name = {d.name: d for d in definitions}
    assessed = []
    if card_label:
        own = by_name.get(TARGET) or InputDefinition(name=TARGET, label="", input_type=InputType.RESOURCE)
        assessed.append(own.model_copy(update={"label": card_label, "input_type": InputType.RESOURCE,
                                               "required": required}))
    if local_label:
        assessed.append(by_name[plugin.local_model_input].model_copy(update={"label": local_label,
                                                                            "required": required}))
    helper_names = {helper_input_name(role): role for role in helper_roles_of(plugin)}
    helpers = [by_name[name].model_copy(update={"label": _helper_label(role)})
               for name, role in helper_names.items() if name in by_name]
    taken = {d.name for d in assessed} | helper_names.keys()
    return [*assessed, *(d for d in definitions if d.name not in taken), *helpers]


async def _ensure_system_target_quietly() -> None:
    try:
        await ensure_system_target()
    except Exception:  # the definitions matter more than the mirror; the platform makes it too
        logger.exception("the system target of this project could not be made sure of")


@router.get("/{plugin_pid}/input_definitions", response=list[InputDefinition])
async def input_definitions_with_target(request, plugin_pid: uuid.UUID):
    definitions = list(await upstream_input_definitions(request, plugin_pid))
    if not _configurator():
        return definitions
    plugin = await loaded_plugin(plugin_pid)
    kinds = target_kinds_of(plugin)
    if kinds:
        if CARD in kinds:
            await _ensure_system_target_quietly()
        return form_inputs(definitions, plugin)
    if any(d.name == TARGET for d in definitions):
        # a plugin's own target (declared optional, for standalone) is required here
        return [d.model_copy(update={"required": True}) if d.name == TARGET else d for d in definitions]
    await _ensure_system_target_quietly()
    return [*definitions,
            InputDefinition(name=TARGET, label=TARGET_LABEL, input_type=InputType.RESOURCE, required=True)]


# What each installed test declares about what it assesses: the platform's target page lists, under
# each target, the tests that can assess it (an AI card target; one that calls it only if connected).
# The plugin check (scripts/verify-plugins.sh) reads the same list: whether the test adopted @assesses
# (an older declaration alone reads as card), its helper roles, and the names of the config fields its
# form asks a person for, so a test still asking for its target there is caught. Names only, never values.

@sync_to_async
def _enabled_plugins() -> list:
    from aisc_backend.models import Plugin

    return list(Plugin.objects.filter(enabled=True).order_by("package_name", "name"))


def _helper_roles(plugin) -> list[str]:
    """The roles of @helper_model, then of any helper:<role> input declared by hand."""
    roles = list(helper_roles_of(plugin))
    for d in getattr(plugin, "_input_definitions", []):
        role = d.name[len(HELPER_INPUT_PREFIX):] if d.name.startswith(HELPER_INPUT_PREFIX) else None
        if role and role not in roles:
            roles.append(role)
    return roles


def _properties(node: dict, defs: dict, seen: frozenset) -> tuple[dict, frozenset]:
    """The properties of a schema node, through $ref, allOf, anyOf and oneOf (a reference followed once)."""
    ref = node.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        if name in seen or name not in defs:
            return {}, seen
        return _properties(defs[name], defs, seen | {name})
    props = dict(node.get("properties") or {})
    for key in ("allOf", "anyOf", "oneOf"):
        for member in node.get(key) or ():
            more, seen = _properties(member, defs, seen)
            props.update(more)
    return props, seen


def _asked_fields(node: dict, ui: dict, defs: dict, prefix: str, seen: frozenset) -> list[str]:
    names = []
    props, seen = _properties(node, defs, seen)
    for name, sub in props.items():
        sub_ui = ui.get(name) if isinstance(ui.get(name), dict) else {}
        if sub_ui.get("ui:widget") == "hidden":  # filled by the platform, not asked of a person
            continue
        nested = _asked_fields(sub, sub_ui, defs, f"{prefix}{name}.", seen)
        names.extend(nested or [prefix + name])
    return names


def config_fields(plugin) -> list[str]:
    """The names of the config fields the plugin's form asks a person for (dotted when nested), read from
    the schema and ui schema the engine's form shows (on_config_change with no data, as the config
    routes of routers/plugin.py): the hidden ones are left out, and no default or value is read."""
    _, schema, ui = plugin.on_config_change(None)
    return sorted(_asked_fields(schema, ui or {}, schema.get("$defs") or {}, "", frozenset()))


def _readable_config_fields(plugin) -> tuple[list[str], bool]:
    """(config_fields, unreadable): a form that cannot be built leaves the test listed, flagged."""
    try:
        return config_fields(plugin), False
    except Exception:
        logger.exception("the config form of %s could not be built for its declarations", type(plugin).__name__)
        return [], True


def declaration(row, plugin) -> dict:
    kinds = target_kinds_of(plugin)
    fields, unreadable = _readable_config_fields(plugin)
    return {"pid": str(row.pid), "name": row.name, "display_name": row.display_name,
            "package_name": row.package_name, "version": row.version,
            "target_kinds": list(kinds), "card_protocols": list(card_protocols_of(plugin)),
            "card_needs_connection": CARD in kinds and card_needs_connection(plugin),
            "adopted": getattr(plugin, "target_kinds", None) is not None,
            "helper_roles": _helper_roles(plugin), "config_fields": fields,
            "config_fields_unreadable": unreadable}


@router.get("/declarations", response=list[dict])
async def plugin_declarations(request):
    out = []
    for row in await _enabled_plugins():
        try:
            plugin = plugin_routes.plugin_loader.load_plugin(row.package_name, row.name, row.version)
            out.append(declaration(row, plugin))
        except Exception:  # one test that does not load, or whose declaration is odd, must not hide the others
            logger.exception("plugin %s %s: its declarations could not be read", row.package_name, row.name)
    return out
