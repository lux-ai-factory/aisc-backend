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
assessed. A plugin that declares nothing keeps the form as it was. Whatever the plugin declares, a
helper model's input has the type `helper`, so no AI System component is ever listed for it: it is
picked among the project's helper models, which the platform keeps (routers/helper_models.py).

The system target is made sure of here, so there is one to pick even before anybody has opened the
platform's targets page: the platform finds it again by its value and renames it. Not before the
project has an AI card version, though: a project used only for the engine has no AI card targets.
"""
import logging
import uuid
from typing import Literal

from asgiref.sync import sync_to_async
from django.conf import settings
from ninja import Router

from aisc_backend import deployment
from aisc_backend import plugin_declarations as stored_declarations
from aisc_backend.repositories.system_version_repository import latest_system_pid_sync
from aisc_backend.routers import plugin as plugin_routes
from aisc_backend.routers.plugin import get_plugin_input_definitions as upstream_input_definitions
from aisc_plugin_interface import InputDefinition, InputType
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


#: the type of a helper model's input on the form: picked among the platform's helper models
HELPER = "helper"


class FormInputDefinition(InputDefinition):
    """An input definition as the evaluation form reads it: a plugin's own types, or `helper`."""
    input_type: InputType | Literal["helper"]


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


async def loaded_plugin(plugin_pid: uuid.UUID) -> stored_declarations.Declared:
    """The plugin as its stored manifest declares it (the engine never imports plugin code)."""
    plugin = await plugin_routes.plugin_repository.get(plugin_pid)
    return await stored_declarations.declared(plugin)


def _kinds(plugin) -> tuple[str, ...]:
    return tuple(plugin.declaration.get("target_kinds") or ())


def _helper_label(role: str) -> str:
    return f"Helper model · {role.capitalize()} (not assessed)"


def form_inputs(definitions: list[InputDefinition], plugin) -> list[InputDefinition]:
    """What is assessed (only the kinds the plugin declares), the plugin's other inputs, its helper models."""
    kinds = _kinds(plugin)
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
    helper_names = {helper_input_name(role): role for role in plugin.declaration.get("helper_roles") or ()}
    helpers = [by_name[name].model_copy(update={"label": _helper_label(role)})
               for name, role in helper_names.items() if name in by_name]
    taken = {d.name for d in assessed} | helper_names.keys()
    return [*assessed, *(d for d in definitions if d.name not in taken), *helpers]


async def _ensure_system_target_quietly() -> None:
    try:
        await ensure_system_target()
    except Exception:  # the definitions matter more than the mirror; the platform makes it too
        logger.exception("the system target of this project could not be made sure of")


def _for_the_form(definitions: list[InputDefinition]) -> list[FormInputDefinition]:
    """The definitions with every helper model's input typed `helper`, whatever type the plugin gave it."""
    return [FormInputDefinition(**{**d.model_dump(),
                                   **({"input_type": HELPER} if d.name.startswith(HELPER_INPUT_PREFIX) else {})})
            for d in definitions]


@router.get("/{plugin_pid}/input_definitions", response=list[FormInputDefinition])
async def input_definitions_with_target(request, plugin_pid: uuid.UUID):
    definitions = list(await upstream_input_definitions(request, plugin_pid))
    if not _configurator():
        return definitions
    return _for_the_form(await _arranged(definitions, plugin_pid))


async def _arranged(definitions: list[InputDefinition], plugin_pid: uuid.UUID) -> list[InputDefinition]:
    """The plugin's inputs in the configurator's order: what is assessed first, its helper models last."""
    plugin = await loaded_plugin(plugin_pid)
    kinds = _kinds(plugin)
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
# form asks a person for, so a test still asking for its target there is caught; for each hidden field
# only whether its default is empty. Names and that flag only, never a value.

@sync_to_async
def _enabled_plugins() -> list:
    from aisc_backend.models import Plugin

    return list(Plugin.objects.filter(enabled=True).order_by("package_name", "name"))


def _helper_roles(plugin) -> list[str]:
    """The roles of @helper_model, then of any helper:<role> input declared by hand."""
    roles = list(plugin.declaration.get("helper_roles") or ())
    for d in plugin.input_definitions:
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


def _empty(value) -> bool:
    return value is None or value == "" or (isinstance(value, (list, dict)) and all(
        _empty(v) for v in (value.values() if isinstance(value, dict) else value)))


_NO_DEFAULT = object()  # the field states no default
_NO_VALUE = object()  # a parent's default is None: the field has no value


def _walk_fields(node: dict, ui: dict, defs: dict, prefix: str, seen: frozenset, default, hidden: bool,
                 asked: list, hidden_fields: dict) -> None:
    """Every leaf field of the form: its dotted name into ``asked``, or, when the form hides it (it or a
    parent with ui:widget hidden), into ``hidden_fields`` with whether its default is empty. A parent's
    default (a nested model's instance) gives its fields' defaults, as Pydantic does; a parent whose
    default is None gives its fields no value, whatever their own defaults."""
    props, seen = _properties(node, defs, seen)
    for name, sub in props.items():
        sub_ui = ui.get(name) if isinstance(ui.get(name), dict) else {}
        sub_hidden = hidden or sub_ui.get("ui:widget") == "hidden"  # filled by the platform, not asked
        if default is _NO_VALUE:
            sub_default = _NO_VALUE
        elif isinstance(default, dict) and name in default:
            sub_default = default[name]
        else:
            sub_default = sub.get("default", _NO_DEFAULT)
        if _properties(sub, defs, seen)[0]:
            below = _NO_VALUE if sub_default is None else sub_default
            _walk_fields(sub, sub_ui, defs, f"{prefix}{name}.", seen, below, sub_hidden, asked, hidden_fields)
        elif sub_hidden:
            hidden_fields[prefix + name] = sub_default in (_NO_VALUE, _NO_DEFAULT) or _empty(sub_default)
        else:
            asked.append(prefix + name)


def config_fields(plugin) -> tuple[list[str], dict[str, bool]]:
    """The config fields of the form the engine shows first (the manifest's form, which is the plugin's
    on_config_change with no data), dotted when nested: the names a person is asked for, and for each
    hidden one only whether its default is empty (a hidden default of its own would be a fallback)."""
    schema, ui = plugin.get_full_schema()
    asked, hidden = [], {}
    _walk_fields(schema, ui or {}, schema.get("$defs") or {}, "", frozenset(), None, False, asked, hidden)
    return sorted(asked), dict(sorted(hidden.items()))


def _readable_config_fields(plugin) -> tuple[list[str], dict[str, bool], bool]:
    """(asked, hidden, unreadable): a form that cannot be built leaves the test listed, flagged."""
    try:
        return (*config_fields(plugin), False)
    except Exception:
        logger.exception("the config form of %s could not be built for its declarations", plugin.row)
        return [], {}, True


def declaration(row, plugin) -> dict:
    kinds = _kinds(plugin)
    fields, hidden, unreadable = _readable_config_fields(plugin)
    return {"pid": str(row.pid), "name": row.name, "display_name": row.display_name,
            "package_name": row.package_name, "version": row.version,
            "target_kinds": list(kinds), "card_protocols": list(plugin.declaration.get("card_protocols") or ()),
            "card_needs_connection": CARD in kinds and bool(plugin.declaration.get("card_needs_connection")),
            "adopted": plugin.adopted,
            "helper_roles": _helper_roles(plugin), "config_fields": fields,
            "hidden_config_fields": hidden, "config_fields_unreadable": unreadable}


@router.get("/declarations", response=list[dict])
async def plugin_declarations(request):
    out = []
    for row in await _enabled_plugins():
        try:
            out.append(declaration(row, await stored_declarations.declared(row)))
        except Exception:  # one test that does not load, or whose declaration is odd, must not hide the others
            logger.exception("plugin %s %s: its declarations could not be read", row.package_name, row.name)
    return out


# ---- which tests cannot be launched yet (the launch page greys them out) ----

#: why a test that calls its target cannot run: the same advice as the launch check (routers/evaluation.py)
NO_CONNECTION = ("No target of the AI card has a connection yet: set one under Manage, Components and "
                 "connections, then this test can be launched")


def needs_a_connected_target(plugin) -> bool:
    """Whether the test can only assess an AI card target that has a connection: it calls the target, and
    has no local model file to assess instead."""
    return _kinds(plugin) == (CARD,) and bool(plugin.declaration.get("card_needs_connection"))


def card_target_connected(platform_pid, key: str) -> bool | None:
    """The platform's answer on one card target (routers/evaluation.py, which imports this module)."""
    from aisc_backend.routers.evaluation import card_target_connected as ask

    return ask(platform_pid, key)


def card_target_keys(platform_pid, values) -> list[str]:
    """The keys of this project's card targets, from the values of its mirrors (`target:<pid>/<key>`)."""
    own = f"target:{platform_pid}/"
    return [v[len(own):] for v in values if isinstance(v, str) and v.startswith(own)]


@sync_to_async
def _card_targets() -> tuple[str | None, list[str]]:
    """(platform pid, the keys of the card targets in use) of the open project; (None, []) when the project
    is not one of the platform's."""
    from aisc_backend.models import AIComponent, Project

    project = Project.objects.filter(platform_project_id__isnull=False).first()
    if project is None:
        return None, []
    values = [c.json_value.get("value") if isinstance(c.json_value, dict) else None
              for c in AIComponent.objects.filter(system__project=project, component_type="resource",
                                                  retired_at__isnull=True)]
    return str(project.platform_project_id), card_target_keys(project.platform_project_id, values)


def some_target_connected(platform_pid, keys: list[str]) -> bool | None:
    """True once one target has a connection, False when the platform says none has, None when it could not
    tell for some target and none is connected (the launch check then decides, as before)."""
    unknown = False
    for key in keys:
        answer = card_target_connected(platform_pid, key)
        if answer is True:
            return True
        if answer is None:
            unknown = True
    return None if unknown else False


@router.get("/launch-blockers", response=dict[str, str])
async def launch_blockers(request):
    """Why each test of the open project cannot be launched yet, by plugin pid; a test not named can be."""
    needing = []
    for row in await _enabled_plugins():
        try:
            if needs_a_connected_target(await stored_declarations.declared(row)):
                needing.append(str(row.pid))
        except Exception:  # one test that does not load must not hide the others
            logger.exception("plugin %s %s: its declarations could not be read", row.package_name, row.name)
    if not needing:
        return {}
    platform_pid, keys = await _card_targets()
    if platform_pid is None:
        return {}
    if await sync_to_async(some_target_connected)(platform_pid, keys) is not False:
        return {}
    return {pid: NO_CONNECTION for pid in needing}
