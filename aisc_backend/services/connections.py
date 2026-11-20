"""
Connections, helpers and run bindings (CON-01) as the API reports them.

A `Connection` is the pilot's connection record: a protocol the adapter registry
can dispatch on, an endpoint, and the protocol's own fields. The secret is never
mirrored here; only its presence and the *names* of the secret headers are, so a
listing can never leak a value.
"""
from __future__ import annotations

import uuid

from aisc_backend import adapters
from aisc_backend.models.ai_system import AIComponent
from aisc_backend.models.connection import Connection, Helper, RunBinding
from aisc_evaluation.models import EvaluationPlugin
from aisc_backend.models.project import Project
from aisc_backend.services import connection_secrets as secrets

#: The helper roles a test may ask for.
HELPER_ROLES = ("judge", "attacker", "grader", "embedder", "generator")


def target_row(component: AIComponent | None) -> dict | None:
    if component is None:
        return None
    return {
        "key": str(component.pid),
        "name": component.name,
        "component_type": component.component_type,
    }


async def connection_row(connection: Connection) -> dict:
    return {
        "name": connection.name,
        "label": connection.label or connection.name,
        "kind": connection.kind,
        "protocol": adapters.describe(connection),
        "base_url": connection.base_url,
        "model": connection.model,
        "supports_tools": connection.supports_tools,
        "supports_logprobs": connection.supports_logprobs,
        "protocol_version": connection.protocol_version,
        "path": connection.path,
        "method": connection.method,
        "body_template": connection.body_template,
        "response_path": connection.response_path,
        "headers": connection.headers or {},
        "secret_header": connection.secret_header,
        "timeout_s": connection.timeout_s,
        "settings": connection.settings or {},
        "target": target_row(connection.target_component),
        "has_secret": await secrets.connection_key_set(connection),
        "secret_headers": await secrets.secret_header_names(connection),
        "last_test_at": connection.last_test_at,
        "last_test_ok": connection.last_test_ok,
    }


async def helper_row(helper: Helper) -> dict:
    return {
        "name": helper.name,
        "label": helper.label or helper.name,
        "endpoint_url": helper.endpoint_url,
        "roles": list(helper.roles or []),
        "has_key": await secrets.helper_key_set(helper),
    }


def binding_row(binding: RunBinding) -> dict:
    return {
        "role": binding.role,
        "kind": binding.kind,
        "adapter_version": binding.adapter_version,
        "base_url": binding.base_url,
        "model": binding.model,
        "connection": binding.connection.name if binding.connection else None,
        "helper": binding.helper.name if binding.helper else None,
    }


async def target_for(project: Project, key: str) -> AIComponent | None:
    try:
        component_pid = uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        return None
    return await AIComponent.objects.filter(pid=component_pid, system__project=project).afirst()


def adapter_version_of(connection: Connection | None) -> str:
    """The revision of the adapter a run used: the built-in revision for one of the
    four protocols, or the installed adapter's own version, which its connection
    carries in `settings`."""
    if connection is None:
        return ""
    if connection.kind in adapters.ADAPTERS:
        return adapters.BUILTIN_VERSION
    return str((connection.settings or {}).get("adapter_version") or "")


async def bind(
    plugin: EvaluationPlugin,
    *,
    role: str = "",
    connection: Connection | None = None,
    helper: Helper | None = None,
) -> RunBinding:
    """Record what this run used, as a snapshot: the adapter/version and endpoint
    are copied here, so editing the connection later does not rewrite the run."""
    defaults = {
        "connection": connection,
        "helper": helper,
        "kind": connection.kind if connection else "openai",
        "adapter_version": adapter_version_of(connection),
        "base_url": connection.base_url if connection else (helper.endpoint_url if helper else ""),
        "model": connection.model if connection else "",
        "name": f"{plugin.name}:{role or 'assessed'}",
        "description": "",
    }
    binding, _ = await RunBinding.objects.aupdate_or_create(
        evaluation_plugin=plugin, role=role, defaults=defaults,
    )
    return binding
