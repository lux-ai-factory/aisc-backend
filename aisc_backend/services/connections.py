"""
Connections and adapters (CON-01), read from the one connection/adapter model
(`AIComponent`).

A "connection" is an `AIComponent` a test can reach: an LLM endpoint, a model
file, a dataset or a generic resource. How a test reaches it is the component's
`adapter` (openai / a2a / rest / builtin); an unset adapter falls back to the
type's default. `helper_role` marks a component that stands in for a helper a
test asks for (a judge, an embedder).

The secret is not mirrored here: it stays in the component's configuration and
only its presence is reported, so a listing can never leak a value.
"""
from __future__ import annotations

from aisc_backend.models.ai_system import AIComponent, AIComponentType

#: The adapters a connection may use (the pilot's connection kinds).
ADAPTERS = ("builtin", "openai", "a2a", "rest")

#: The adapter a connection uses when none is chosen, by component type.
DEFAULT_ADAPTER = {
    AIComponentType.LLM: "openai",
    AIComponentType.MODEL: "builtin",
    AIComponentType.DATASET: "builtin",
    AIComponentType.RESOURCE: "builtin",
}

#: The keys a component's configuration may carry a secret under.
_SECRET_KEYS = ("secret_key", "api_key", "key")


def adapter_of(component: AIComponent) -> str:
    return component.adapter or DEFAULT_ADAPTER.get(component.component_type, "builtin")


def endpoint_of(component: AIComponent) -> str:
    value = component.json_value or {}
    return str(value.get("endpoint_url") or value.get("value") or "")


def key_set(component: AIComponent) -> bool:
    value = component.json_value or {}
    return any(bool(value.get(name)) for name in _SECRET_KEYS)


def connection_row(component: AIComponent) -> dict:
    return {
        "pid": component.pid,
        "name": component.name,
        "component_type": component.component_type,
        "adapter": adapter_of(component),
        "adapter_version": component.adapter_version,
        "helper_role": component.helper_role,
        "endpoint": endpoint_of(component),
        "key_set": key_set(component),
    }
