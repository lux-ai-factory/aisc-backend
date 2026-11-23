"""
Component secrets (ADR 0009), in the project's one secret store.

A key is never a column of a component: it is a `ProjectConfig` of category
`secrets`, encrypted with the platform's key (`utils.encryption`), scoped to the
project. A listing may report that a key is stored and the *names* of stored secret
headers; it can never read a value back. Resolving a value is a separate call, made
only when the adapter is about to use it.

This replaces the CON-01 `connection_secrets` service: the same store, with keys
named after the component instead of the connection.
"""
from __future__ import annotations

from aisc_backend.models.ai_system import AIComponent
from aisc_backend.models.project_config import ProjectConfig, ProjectConfigCategory
from aisc_backend.utils.encryption import decrypt_value, encrypt_value

_CATEGORY = ProjectConfigCategory.SECRETS

_KEY_SUFFIX = "key"
_HEADER_MARK = "header"


def component_key_name(pid) -> str:
    return f"component:{pid}:{_KEY_SUFFIX}"


def component_header_name(pid, header: str) -> str:
    return f"component:{pid}:{_HEADER_MARK}:{header}"


def _mask(value: str) -> str:
    return "•" * min(len(value), 12)


async def _row(project_id, key: str) -> ProjectConfig | None:
    return await ProjectConfig.objects.filter(project_id=project_id, category=_CATEGORY, key=key).afirst()


async def set_secret(project_id, key: str, value: str) -> ProjectConfig:
    row = await _row(project_id, key)
    if row is None:
        return await ProjectConfig.objects.acreate(
            name=key, description="", project_id=project_id, category=_CATEGORY, key=key,
            encrypted_value=encrypt_value(value), masked_value=_mask(value),
        )
    row.encrypted_value = encrypt_value(value)
    row.masked_value = _mask(value)
    await row.asave()
    return row


async def get_secret(project_id, key: str) -> str | None:
    row = await _row(project_id, key)
    if row is None or not row.encrypted_value:
        return None
    return decrypt_value(row.encrypted_value)


async def clear_secret(project_id, key: str) -> None:
    await ProjectConfig.objects.filter(project_id=project_id, category=_CATEGORY, key=key).adelete()


async def secret_exists(project_id, key: str) -> bool:
    return await ProjectConfig.objects.filter(
        project_id=project_id, category=_CATEGORY, key=key,
    ).aexists()


async def set_secret_header(component: AIComponent, header: str, value: str) -> None:
    key = component_header_name(component.pid, header)
    row = await _row(component.project_id, key)
    if row is None:
        await ProjectConfig.objects.acreate(
            name=key, description="", project_id=component.project_id, category=_CATEGORY,
            key=key, encrypted_value=encrypt_value(value), masked_value=_mask(value),
            json_value={"header": header},
        )
        return
    row.encrypted_value = encrypt_value(value)
    row.masked_value = _mask(value)
    row.json_value = {"header": header}
    await row.asave()


async def remove_secret_header(component: AIComponent, header: str) -> None:
    await clear_secret(component.project_id, component_header_name(component.pid, header))


async def secret_header_names(component: AIComponent) -> list[str]:
    prefix = f"component:{component.pid}:{_HEADER_MARK}:"
    rows = [
        row
        async for row in ProjectConfig.objects.filter(
            project_id=component.project_id, category=_CATEGORY, key__startswith=prefix,
        )
    ]
    return sorted((row.json_value or {}).get("header") or row.key[len(prefix):] for row in rows)


async def component_key_set(component: AIComponent) -> bool:
    """Whether a key is stored for this component, under its own key name or the
    key it references (a component may point at an existing project secret)."""
    if component.secret_key:
        return await secret_exists(component.project_id, component.secret_key)
    return await secret_exists(component.project_id, component_key_name(component.pid))
