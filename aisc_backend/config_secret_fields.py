"""Which fields of a saved plugin configuration are keys, and what the form shows for a key kept elsewhere.

A key field is taken out of the configuration to store and kept as a project secret instead (category secrets,
encrypted), linked to the configuration by a mapping whose `plugin_config_key` is `config.<path>`. At run time the
worker hands the key to the plugin's own process in the variable `secret_env_name(path)`, and AISC's runtime
script puts it back at its path in memory just before `evaluate`.

What a key is: a non-empty string the plugin's form marks (format password, writeOnly, the password widget), or
one the stored-configuration rule of `config_secrets` takes for a secret by its name and value. A plugin that
adopted @assesses takes no key at all.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from aisc_backend.config_secrets import _holds_string, _path, _stored_secret, stored_secret_name

#: The `plugin_config_key` prefix of a field kept as a project secret.
CONFIG_SECRET_PREFIX = "config."
#: The key fields one configuration may hold (the platform accepts as many secret events for one save).
MAX_SECRET_FIELDS = 16
SECRET_HELP = "Kept as the project secret {key}. Leave this empty to keep it; type a new key to replace it."
SECRET_DESCRIPTION = "A key from this plugin's configuration form, kept encrypted as a project secret"

ADOPTED_REFUSAL = ("this plugin takes no key in its configuration: clear {fields}. A helper model (judge, attacker,"
                   " grader, embedder, generator) gets its key from the project secret bound to its helper model"
                   " input, and the system under test is reached through its connection in Manage > Connections")
NOT_TEXT = "the key field {fields} must hold one key as text, not a list or an object"
BAD_NAME = "the key field {path} has a name with '.', '[' or ']', which a key field cannot have"
SAME_NAME = "the key fields {first} and {second} would be kept under the same secret name; rename one"
TOO_MANY = "a configuration may hold at most 16 key fields; this one holds {count}"
RESTORE_REFUSED = ("configuration {config_id} holds a key in clear ({fields}): open the form, type the key again"
                   " and save, so the key is kept as a project secret")

_PATH_STEP = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_NAME_BREAKS_PATH = re.compile(r"[.\[\]]")


class SecretFieldRefused(Exception):
    """A save or restore the rule refuses; str(exc) is the message the route answers with."""


@dataclass(frozen=True)
class SplitConfig:
    kept: dict                 # the configuration to store
    moved: dict[str, str]      # path -> key value, in traversal order
    not_text: list[str]        # paths of secret fields whose value is a dict or list holding text
    #: path -> the field's own name in its dict, for the moved fields under a name (a list item has none).
    names: dict[str, str] = field(default_factory=dict, compare=False, repr=False)


def resolved(node, defs: dict) -> dict:
    """A JSON schema node with its $ref followed and its anyOf/allOf/oneOf merged (an optional
    field is anyOf [its type, null])."""
    if not isinstance(node, dict):
        return {}
    if "$ref" in node:
        node = {**defs.get(node["$ref"].rsplit("/", 1)[-1], {}), **{k: v for k, v in node.items() if k != "$ref"}}
    merged = dict(node)
    for branch in ("anyOf", "allOf", "oneOf"):
        for part in node.get(branch) or []:
            part = resolved(part, defs)
            merged.setdefault("properties", {}).update(part.get("properties") or {})
            for key in ("format", "writeOnly", "items"):
                if key in part and key not in merged:
                    merged[key] = part[key]
    return merged


def _marked(field_schema: dict, field_ui) -> bool:
    return (field_schema.get("format") == "password" or field_schema.get("writeOnly") is True
            or (isinstance(field_ui, dict) and field_ui.get("ui:widget") == "password"))


class _Splitter:
    def __init__(self, defs: dict):
        self.defs = defs
        self.moved: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.not_text: list[str] = []

    def walk_dict(self, value: dict, schema: dict, ui, prefix: str) -> dict:
        properties = schema.get("properties") or {}
        kept = {}
        for name, inner in value.items():
            path = _path(prefix, name)
            field_schema = resolved(properties.get(name), self.defs)
            field_ui = ui.get(name) if isinstance(ui, dict) else None
            marked = _marked(field_schema, field_ui)
            kind = stored_secret_name(str(name))
            if isinstance(inner, str) and inner:
                if marked or _stored_secret(kind, inner):
                    self.moved[path] = inner
                    self.names[path] = str(name)
                    continue
                kept[name] = inner
            elif isinstance(inner, (dict, list)):
                if (((marked or kind == "strong") and _holds_string(inner))
                        or (kind == "weak" and _stored_secret(kind, inner))):
                    self.not_text.append(path)
                    kept[name] = copy.deepcopy(inner)
                else:
                    kept[name] = self.walk(inner, field_schema, field_ui, path)
            else:
                kept[name] = inner
        return kept

    def walk_list(self, value: list, schema: dict, ui, prefix: str) -> list:
        items = resolved(schema.get("items"), self.defs)
        ui_items = ui.get("items") if isinstance(ui, dict) else None
        kept = []
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            if isinstance(item, str):
                if _stored_secret(None, item):
                    self.moved[path] = item
                    kept.append("")
                else:
                    kept.append(item)
            else:
                kept.append(self.walk(item, items, ui_items, path))
        return kept

    def walk(self, value, schema: dict, ui, prefix: str):
        if isinstance(value, dict):
            return self.walk_dict(value, schema, ui, prefix)
        if isinstance(value, list):
            return self.walk_list(value, schema, ui, prefix)
        return value


def split_secret_fields(config: dict, schema: dict, ui: dict) -> SplitConfig:
    """The configuration to store without its key fields, the key fields by path, and the key fields that hold a
    list or an object instead of one key. `config` is not changed."""
    schema = schema if isinstance(schema, dict) else {}
    splitter = _Splitter(schema.get("$defs") or {})
    kept = splitter.walk_dict(config, schema, ui if isinstance(ui, dict) else {}, "")
    return SplitConfig(kept=kept, moved=splitter.moved, not_text=splitter.not_text, names=splitter.names)


def secret_env_name(path: str) -> str:
    """The variable the worker sets for a field kept at `path` (its `config.<path>` mapping's name, as
    `build_secret_environment` turns a `plugin_config_key` into a variable)."""
    return "AISC_SECRET_" + "".join(c.upper() if c.isalnum() else "_" for c in CONFIG_SECRET_PREFIX + path)


def check_split(split: SplitConfig, adopted: bool) -> None:
    """Raises SecretFieldRefused when the configuration cannot be stored with its keys kept as secrets."""
    if adopted and (split.moved or split.not_text):
        raise SecretFieldRefused(ADOPTED_REFUSAL.format(fields=", ".join(list(split.moved) + split.not_text)))
    if split.not_text:
        raise SecretFieldRefused(NOT_TEXT.format(fields=", ".join(split.not_text)))
    for path in split.moved:
        name = split.names.get(path)
        if name is not None and _NAME_BREAKS_PATH.search(name):
            raise SecretFieldRefused(BAD_NAME.format(path=path))
    seen: dict[str, str] = {}
    for path in split.moved:
        env = secret_env_name(path)
        if env in seen:
            raise SecretFieldRefused(SAME_NAME.format(first=seen[env], second=path))
        seen[env] = path
    if len(split.moved) > MAX_SECRET_FIELDS:
        raise SecretFieldRefused(TOO_MANY.format(count=len(split.moved)))


def secret_hints(ui_schema: dict, keys: dict[str, str]) -> dict:
    """A copy of `ui_schema` where each field kept as a secret (`path -> secret key`) is a password input with the
    secret named under it. A path through a list gets no hint."""
    hinted = copy.deepcopy(ui_schema) if isinstance(ui_schema, dict) else {}
    for path, key in keys.items():
        if "[" in path:
            continue
        node = hinted
        for name in path.split("."):
            if not isinstance(node.get(name), dict):
                node[name] = {}
            node = node[name]
        node["ui:help"] = SECRET_HELP.format(key=key)
        node.setdefault("ui:widget", "password")
    return hinted


def path_steps(path: str) -> list[str | int]:
    """`items[0].token` -> ["items", 0, "token"]."""
    return [name if name else int(index) for name, index in _PATH_STEP.findall(path)]


def value_at(config, path: str, default=None):
    """The value at `path` in `config`, or `default` when a step is missing."""
    node = config
    for step in path_steps(path):
        if isinstance(step, int):
            if not isinstance(node, list) or step >= len(node):
                return default
        elif not isinstance(node, dict) or step not in node:
            return default
        node = node[step]
    return node


def with_set_at(config, paths, marker):
    """A copy of `config` with `marker` at each path: missing dicts are created, a list index beyond its list is
    skipped, and a step that is not a container (a value the masking already replaced) becomes the marker."""
    out = copy.deepcopy(config)
    for path in paths:
        steps = path_steps(path)
        node = out
        for position, step in enumerate(steps):
            last = position == len(steps) - 1
            if isinstance(step, int):
                if not isinstance(node, list) or step >= len(node):
                    break
            elif not isinstance(node, dict):
                break
            if last:
                node[step] = marker
                break
            child = node[step] if isinstance(step, int) else node.get(step)
            if child is None and not isinstance(step, int):
                child = node[step] = {}
            if not isinstance(child, (dict, list)):
                node[step] = marker
                break
            node = child
    return out
