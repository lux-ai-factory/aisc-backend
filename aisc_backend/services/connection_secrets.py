"""
Connection and helper secrets (CON-01), in the project's one secret store.

A key is never a field of a connection row: it is a `ProjectConfig` of category
`secrets`, encrypted with the platform's key (`utils.encryption`), scoped to the
project. A listing may report that a key is stored and the *names* of stored secret
headers; it can never read a value back. Resolving a value is a separate call, made
only when the adapter is about to use it.
"""
from __future__ import annotations

from aisc_backend.models.connection import Connection, Helper
from aisc_backend.models.project_config import ProjectConfig, ProjectConfigCategory
from aisc_backend.utils.encryption import decrypt_value, encrypt_value

_CATEGORY = ProjectConfigCategory.SECRETS

_KEY_SUFFIX = "key"
_HEADER_MARK = "header"


def connection_key_name(name: str) -> str:
    return f"connection:{name}:{_KEY_SUFFIX}"


def connection_header_name(name: str, header: str) -> str:
    return f"connection:{name}:{_HEADER_MARK}:{header}"


def helper_key_name(name: str) -> str:
    return f"helper:{name}:{_KEY_SUFFIX}"


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


async def set_secret_header(connection: Connection, header: str, value: str) -> None:
    key = connection_header_name(connection.name, header)
    row = await _row(connection.project_id, key)
    if row is None:
        await ProjectConfig.objects.acreate(
            name=key, description="", project_id=connection.project_id, category=_CATEGORY,
            key=key, encrypted_value=encrypt_value(value), masked_value=_mask(value),
            json_value={"header": header},
        )
        return
    row.encrypted_value = encrypt_value(value)
    row.masked_value = _mask(value)
    row.json_value = {"header": header}
    await row.asave()


async def remove_secret_header(connection: Connection, header: str) -> None:
    await clear_secret(connection.project_id, connection_header_name(connection.name, header))


async def secret_header_names(connection: Connection) -> list[str]:
    prefix = f"connection:{connection.name}:{_HEADER_MARK}:"
    rows = [
        row
        async for row in ProjectConfig.objects.filter(
            project_id=connection.project_id, category=_CATEGORY, key__startswith=prefix,
        )
    ]
    return sorted((row.json_value or {}).get("header") or row.key[len(prefix):] for row in rows)


async def connection_key_set(connection: Connection) -> bool:
    return await secret_exists(connection.project_id, connection_key_name(connection.name))


async def helper_key_set(helper: Helper) -> bool:
    return await secret_exists(helper.project_id, helper_key_name(helper.name))
