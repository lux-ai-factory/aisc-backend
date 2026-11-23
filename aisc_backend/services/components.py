"""
Components as the API reports them, and the preset/advanced payloads that fill one
(ADR 0009).

An endpoint component is a URL, a protocol, an adapter id/version and that
adapter's config as JSON, with its key in the project's secret store (the
component names the key; it never holds the value). A preset is a starting point:
it fills the columns and the adapter config, and "Custom" opens the adapter-plus-
config form for everything else. Presets are declared in
``aisc_plugin_interface.adapters``, so adding one is a registration.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from aisc_backend.models.ai_system import AIComponent, AIComponentType
from aisc_backend.services import component_secrets as secrets
from aisc_plugin_interface.adapters import (
    BUILTIN_ADAPTERS,
    DataAdapter,
    EndpointAdapter,
    Secret,
    Turn,
    compat,
    preset as get_preset,
)

#: The registry strings that are not (yet) components.
KINDS = tuple(AIComponentType.values)


@dataclass(frozen=True)
class Target:
    """What an endpoint adapter builds a request against: a URL and its protocol.

    The adapter's own config (its model name, headers, templates) is the adapter
    instance's ``config``; the component's kind and adapter id are the caller's.
    """

    url: str = ""
    protocol: str = ""


def builtin_adapter(adapter_id: str) -> type[EndpointAdapter] | None:
    for cls in BUILTIN_ADAPTERS:
        if getattr(cls, "id", "") == adapter_id and issubclass(cls, EndpointAdapter):
            return cls
    return None


def adapter_class(adapter_id: str) -> type[EndpointAdapter] | None:
    """The adapter for an id: the standard library first, then any package the
    plugin-manager discovered ships it (ADR 0009 D-19)."""
    cls = builtin_adapter(adapter_id)
    if cls is not None:
        return cls
    try:
        from aisc_plugins.routers.plugin import plugin_loader
    except Exception:  # pragma: no cover - the loader is present in the app
        return None
    for package, versions in (plugin_loader.list_adapters() or {}).items():
        if package == "aisc-plugin-interface":
            continue
        for version, entry in versions.items():
            for manifest in entry.get("adapters", []):
                if manifest.get("id") == adapter_id and manifest.get("kind") == "endpoint":
                    try:
                        return plugin_loader.load_adapter(package, version, adapter_id)
                    except Exception:
                        return None
    return None


def validate_config(adapter_id: str, config: dict | None) -> dict:
    """Validate a component's adapter config with the adapter's pydantic ``Config``.

    An unknown adapter (one whose package is not importable here) is stored as
    given; the worker validates it against the same ``Config`` at run time.
    """
    cls = adapter_class(adapter_id) if adapter_id else None
    if cls is None:
        return dict(config or {})
    return cls.Config.model_validate(config or {}).model_dump()


def adapter_install_target(adapter_id: str) -> dict | None:
    """Where a worker installs an adapter from and how it imports it, when it is
    not in the standard library (ADR 0009). ``None`` means builtin: the worker
    always installs ``aisc-plugin-interface`` and resolves the id from it."""
    if not adapter_id or builtin_adapter(adapter_id) is not None:
        return None
    try:
        from aisc_plugins.routers.plugin import plugin_loader
    except Exception:  # pragma: no cover - the loader is present in the app
        return None
    for package, versions in (plugin_loader.list_adapters() or {}).items():
        if package == "aisc-plugin-interface":
            continue
        for version, entry in versions.items():
            for manifest in entry.get("adapters", []):
                if manifest.get("id") == adapter_id:
                    return {
                        "package": package,
                        "version": version,
                        "source": entry.get("source", ""),
                        "module": manifest.get("module", ""),
                        "class_name": manifest.get("class", ""),
                    }
    return None


def target_of(component: AIComponent) -> Target:
    return Target(url=component.url or "", protocol=component.protocol or "")


def installed_adapters() -> list[type]:
    """The standard library plus every adapter a discovered package ships (ADR
    0009 D-19). A package that cannot be imported is skipped: the catalogue, not
    this call, reports it."""
    classes: list[type] = list(BUILTIN_ADAPTERS)
    try:
        from aisc_plugins.routers.plugin import plugin_loader
    except Exception:  # pragma: no cover - the loader is present in the app
        return classes
    for package, versions in (plugin_loader.list_adapters() or {}).items():
        if package == "aisc-plugin-interface":
            continue
        for version, entry in versions.items():
            for manifest in entry.get("adapters", []):
                try:
                    classes.append(plugin_loader.load_adapter(package, version, manifest["id"]))
                except Exception:
                    continue
    return classes


def slot_fit(slot, component: AIComponent, adapters: list[type]) -> tuple[bool, list[str]]:
    """Whether a component may fill a plugin slot, and with which adapters.

    One rule, from ``aisc_plugin_interface.adapters.compat`` (ADR 0009). A
    datashape or resource component carries its own value, so it needs no adapter
    and fits a slot that accepts ``json``/``resource`` directly.
    """
    accepts = compat.accepts_of(slot)
    if component.component_type == AIComponentType.ENDPOINT:
        classes = compat.endpoint_slot_candidates(
            component.protocol or "",
            slot,
            [a for a in adapters if issubclass(a, EndpointAdapter)],
        )
        return bool(classes), [a.id for a in classes]
    if component.component_type == AIComponentType.DATASHAPE:
        return "json" in accepts, []
    if component.component_type == AIComponentType.RESOURCE:
        return "resource" in accepts, []
    classes = compat.data_slot_candidates(
        component.format or "",
        slot,
        [a for a in adapters if issubclass(a, DataAdapter)],
    )
    return bool(classes), [a.id for a in classes]


def resolve_input_adapter(
    slot,
    component: AIComponent,
    requested: str | None,
    config: dict | None,
    adapters: list[type],
) -> tuple[str, str, dict]:
    """The adapter for one evaluation input (ADR 0009).

    An explicit request wins unless the slot locks the choice
    (``adapter_required``); otherwise the slot's default, otherwise the first that
    fits. Raises ``ValueError`` when the requested adapter does not fit the
    component. Returns ``(adapter_id, adapter_version, config)``.
    """
    fits, adapter_ids = slot_fit(slot, component, adapters) if slot is not None else (True, [])
    locked = bool(getattr(slot, "adapter_required", False)) and getattr(slot, "adapter", None)
    if locked:
        chosen = slot.adapter
    else:
        chosen = requested or (getattr(slot, "adapter", None) if slot is not None else None)
    if not chosen:
        chosen = adapter_ids[0] if adapter_ids else ""
    if chosen and adapter_ids and chosen not in adapter_ids:
        raise ValueError(
            f"adapter {chosen!r} does not fit component {component.name!r}"
        )
    cls = next((a for a in adapters if getattr(a, "id", "") == chosen), None)
    if cls is not None and config is not None:
        config = cls.Config.model_validate(config).model_dump()
    return chosen, (getattr(cls, "version", "") if cls is not None else ""), dict(config or {})


def provenance_of(
    component: AIComponent, adapter_id: str, adapter_version: str, config: dict
) -> dict:
    """The endpoint snapshot recorded on an input at dispatch (ADR 0009). Secrets
    are never in the config, and the hash is over the config alone."""
    import hashlib
    import json

    digest = hashlib.sha256(
        json.dumps(config or {}, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {
        "url": component.url or "",
        "protocol": component.protocol or "",
        "adapter": adapter_id,
        "version": adapter_version,
        "config_hash": digest,
    }


async def resolve_secret(component: AIComponent) -> Secret:
    """The component's key and secret headers, resolved for one call. The value is
    read only here, immediately before the adapter uses it."""
    key_name = component.secret_key or secrets.component_key_name(component.pid)
    key = await secrets.get_secret(component.project_id, key_name)
    headers: dict[str, str] = {}
    for header in await secrets.secret_header_names(component):
        value = await secrets.get_secret(
            component.project_id, secrets.component_header_name(component.pid, header)
        )
        if value is not None:
            headers[header] = value
    return Secret(key=key, headers=headers)


async def component_row(component: AIComponent) -> dict:
    return {
        "pid": str(component.pid),
        "name": component.name,
        "description": component.description or "",
        "component_type": component.component_type,
        "format": component.format or "",
        "data": component.data or "",
        "file_size": component.file_size,
        "json_value": component.json_value or {},
        "source_dataset_pid": str(component.source_dataset.pid) if component.source_dataset_id else None,
        "url": component.url or "",
        "protocol": component.protocol or "",
        "adapter": component.adapter or "",
        "adapter_version": component.adapter_version or "",
        "adapter_config": component.adapter_config or {},
        "preset": component.preset or "",
        "secret_key": component.secret_key or "",
        "has_secret": await secrets.component_key_set(component),
        "secret_headers": await secrets.secret_header_names(component),
    }


def apply_preset(component: AIComponent, preset_id: str, fields: dict) -> None:
    """Fill a component from a preset's form. The key (``secret``) is handled by
    the caller, which owns the store."""
    form = get_preset(preset_id)
    if form is None:
        raise ValueError(f"unknown preset {preset_id!r}")
    component.component_type = form.kind
    component.preset = preset_id
    if form.protocol:
        component.protocol = form.protocol
    if form.adapter:
        component.adapter = form.adapter
    config = dict(component.adapter_config or {})
    for field in form.fields:
        if field.key not in fields:
            continue
        value = fields[field.key]
        if field.target == "url":
            component.url = str(value or "")
        elif field.target == "config":
            config[field.config_key or field.key] = value
        # target == "secret" is stored by the caller
    component.adapter_config = config


def read_preset(component: AIComponent) -> dict:
    """The values a preset's form shows when reopening the component. The key is
    never read back; only whether one is stored."""
    form = get_preset(component.preset) if component.preset else None
    if form is None:
        return {"preset": component.preset or "", "fields": {}}
    fields: dict[str, Any] = {}
    for field in form.fields:
        if field.target == "url":
            fields[field.key] = component.url or ""
        elif field.target == "config":
            fields[field.key] = (component.adapter_config or {}).get(field.config_key or field.key)
    return {"preset": form.id, "fields": fields}


async def try_component(
    component: AIComponent,
    *,
    input: Any = "ping",
    history: list | None = None,
    params: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """One call through the component's adapter, as a test would make it (the
    pilot's "Try"). Never raises for an HTTP or network failure."""
    cls = builtin_adapter(component.adapter) if component.adapter else None
    if cls is None:
        return {
            "ok": False, "error": "test_runs_on_worker",
            "detail": "this adapter runs on the worker; test it from a run",
            "latency_ms": 0,
        }
    adapter = cls(component.adapter_config or None)
    secret = await resolve_secret(component)
    target = target_of(component)
    request = adapter.build_request(
        target, secret, turn=Turn(input=input, history=history or [], params=params or {})
    )
    start = time.monotonic()
    try:
        if client is not None:
            response = await _send(client, request)
        else:
            async with httpx.AsyncClient(timeout=60) as owned:
                response = await _send(owned, request)
    except httpx.HTTPError as exc:
        return {
            "ok": False, "error": "unreachable", "detail": str(exc),
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
    latency_ms = round((time.monotonic() - start) * 1000)
    answer = adapter.parse_response(response.status_code, response.text, target)
    return {
        "ok": answer.ok,
        "answer": answer.answer,
        "refused": answer.refused,
        "refusal_reason": answer.refusal_reason,
        "error": answer.error,
        "detail": answer.detail,
        "capabilities": answer.capabilities,
        "latency_ms": latency_ms,
    }


async def _send(client: httpx.AsyncClient, request) -> httpx.Response:
    kwargs: dict[str, Any] = {"headers": request.headers}
    if request.json_body is not None:
        kwargs["json"] = request.json_body
    elif request.text_body is not None:
        kwargs["content"] = request.text_body
    return await client.request(request.method, request.url, **kwargs)
