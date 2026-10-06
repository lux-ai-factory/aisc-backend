"""Every engine query goes to the database of one project (isolation 2026-09-25, I7.1).

Each platform project has its own Postgres database, `project_<pid without
hyphens>`, and the engine's tables live in its schema `engine` there, with the
same definitions as before. This module is the only way the ORM reaches one:

- `alias_for(platform_pid)` checks the pid and registers a Django database alias
  for that project's database (the alias is the database name) from
  settings.PROJECT_DATABASE_TEMPLATE; it does no I/O;
- `admitted` holds the alias admitted for the current request. It is a
  ContextVar, so it follows the request through asyncio and sync_to_async;
- `ProjectDatabaseRouter` sends every read, write and migration to that alias,
  and refuses when nothing is admitted: a query is never sent to `default` (the
  dummy backend) or to `platform` (membership and names only);
- `open_alias(alias)` connects, migrates the database the first time this
  process opens it, and turns a missing database into NoSuchProjectDatabase;
- `forget(alias)` drops an alias whose database is gone (I2.5).

Who may be admitted, and when, is the door's decision (aisc_backend.project_door);
this module decides nothing about membership. It also mints and checks the run
ticket the worker carries (I7.3). It names nothing of the platform's schema.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import re
import threading
import time
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, OperationalError, connections

PID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
DATABASE = re.compile(r"^project_[0-9a-f]{32}$")

#: The alias (database name) admitted for the current request, or None.
admitted: ContextVar[str | None] = ContextVar("aisc_admitted_alias", default=None)
#: The platform pid admitted for the current request (read by the door, E2).
admitted_pid: ContextVar[str | None] = ContextVar("aisc_admitted_pid", default=None)


class NotAPid(ValueError):
    """What should name a project is not a pid."""


class NoProjectAdmitted(RuntimeError):
    """An ORM query with no project admitted. Raised before any connection is made."""


class NoSuchProjectDatabase(Exception):
    """The project's database is missing, or the engine may not connect to it."""


class SchemaNotProvisioned(Exception):
    """The database has no `engine` schema the engine may create tables in."""


class MigrationFailed(Exception):
    """Migrating a project database failed; the next open tries again."""


def configurator_databases(env) -> tuple[dict, list[str], dict]:
    """The databases of a Configurator engine on Postgres (isolation I7.1), for config/settings.py.

    Every project has its own database, `project_<pid without hyphens>`, and the
    engine's tables live in its schema `engine` there, so the engine has no database
    of its own:

    - `default` is Django's dummy backend, so a query that was not routed to an
      admitted project fails instead of landing somewhere;
    - `platform` is a raw-SQL connection to the platform database, search_path
      `core`, read only by the membership check and the project-name lookup;
    - one alias per project database, registered on first use from the returned
      template by alias_for(), whose router sends every ORM read, write and
      migration to the alias admitted for the current request.

    DB_NAME names only the platform database. `env` is the settings' environs reader.
    Returns (DATABASES, DATABASE_ROUTERS, PROJECT_DATABASE_TEMPLATE).
    """
    db_schema = env("DB_SCHEMA", "")
    databases = {
        "default": {"ENGINE": "django.db.backends.dummy"},
        "platform": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "platform"),
            "USER": env("DB_USER", ""),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", ""),
            "PORT": env("DB_PORT", ""),
            "OPTIONS": {"options": "-c search_path=core"},
            "CONN_MAX_AGE": 0,
        },
    }
    # Copied for each project database, with NAME = project_<hex>.
    template = {
        "ENGINE": "django.db.backends.postgresql",
        "USER": env("DB_USER", ""),
        "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", ""),
        "PORT": env("DB_PORT", ""),
        "OPTIONS": {"options": f"-c search_path={db_schema or 'engine'}"},
        "CONN_MAX_AGE": 0,
    }
    return databases, ["aisc_backend.projectdb.ProjectDatabaseRouter"], template


_settings_lock = threading.Lock()
_migrate_locks: dict[str, threading.Lock] = {}
_migrate_locks_lock = threading.Lock()
_migrated: set[str] = set()


def enabled() -> bool:
    """Whether the engine runs on project databases (Postgres) or on one database."""
    return bool(getattr(settings, "PROJECT_DATABASES", False))


def normalise(pid) -> str:
    """The pid in lower case, or NotAPid."""
    text = str(pid) if pid is not None else ""
    if not PID.match(text):
        raise NotAPid("not a project pid")
    return text.lower()


def _ticket_key() -> str:
    """RUN_TICKET_KEY, which only the backend holds. Not DJANGO_SECRET_KEY: the eval worker holds that one
    and runs plugin code (2026-10-06). Standalone (one project) keeps SECRET_KEY when it is unset."""
    from aisc_backend import deployment

    if settings.RUN_TICKET_KEY:
        return settings.RUN_TICKET_KEY
    if settings.AISC_DEPLOYMENT == deployment.CONFIGURATOR:
        raise ImproperlyConfigured("RUN_TICKET_KEY is not set: no run ticket can be made or checked")
    return settings.SECRET_KEY


def _now() -> int:
    return int(time.time())


def _signature(platform_pid, evaluation_pid, expires: int) -> str:
    message = f"{normalise(platform_pid)}.{normalise(evaluation_pid)}.{expires}"
    return hmac.new(_ticket_key().encode(), message.encode(), hashlib.sha256).hexdigest()


def run_ticket(platform_pid, evaluation_pid) -> str:
    """I7.3: the run ticket, `<expiry>.<hex HMAC-SHA256 over <platform pid>.<evaluation pid>.<expiry>>`
    with RUN_TICKET_KEY. Minted when an editor starts a run; the worker only carries it, and the door
    checks it on every internal call. It lasts RUN_TICKET_TTL_SECONDS (a day, longer than any run), so one
    seen in a log or kept by a plugin opens nothing for ever (code review 2026-10-06)."""
    expires = _now() + int(getattr(settings, "RUN_TICKET_TTL_SECONDS", 24 * 60 * 60))
    return f"{expires}.{_signature(platform_pid, evaluation_pid, expires)}"


def ticket_is_valid(platform_pid, evaluation_pid, ticket) -> bool:
    """Whether this ticket was minted for this project and this evaluation, and has not expired."""
    expires, _, mac = str(ticket or "").partition(".")
    if not (expires.isdigit() and mac):
        return False
    try:
        expected = _signature(platform_pid, evaluation_pid, int(expires))
    except NotAPid:
        return False
    return hmac.compare_digest(expected, mac) and int(expires) > _now()


def database_name(pid) -> str:
    """I1.8: `project_` + the lower-case pid without hyphens."""
    return "project_" + normalise(pid).replace("-", "")


def pid_of(database: str) -> str:
    """The platform pid a project database is named after."""
    if not DATABASE.match(database or ""):
        raise NotAPid("not a project database name")
    h = database[len("project_"):]
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _replace_settings(new: dict) -> None:
    connections._settings = new
    connections.__dict__["settings"] = new


def alias_for(platform_pid) -> str:
    """The alias of this project's database, registered on first use (no I/O)."""
    alias = database_name(platform_pid)
    if not enabled():
        return DEFAULT_DB_ALIAS
    if alias in connections.settings:
        return alias
    with _settings_lock:
        current = connections.settings
        if alias not in current:
            template = copy.deepcopy(settings.PROJECT_DATABASE_TEMPLATE)
            configured = connections.configure_settings(
                {DEFAULT_DB_ALIAS: {}, alias: {**template, "NAME": alias}})[alias]
            _replace_settings({**current, alias: configured})
    return alias


def forget(alias: str) -> None:
    """Drop an alias (its database was dropped): settings, connection, migrated mark."""
    if not DATABASE.match(alias or ""):
        return
    with _settings_lock:
        current = connections.settings
        if alias in current:
            _replace_settings({k: v for k, v in current.items() if k != alias})
    try:
        conn = getattr(connections._connections, alias)
    except AttributeError:
        conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - the database is gone; nothing to close cleanly
            pass
        try:
            del connections[alias]
        except AttributeError:
            pass
    _migrated.discard(alias)


def _is_gone(exc: BaseException) -> bool:
    """A missing database (3D000) or no CONNECT on it (42501)."""
    cause = exc.__cause__ or exc
    code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    text = str(exc)
    return (code in ("3D000", "42501") or "does not exist" in text
            or "permission denied for database" in text)


def ensure(alias: str) -> None:
    """Connect this alias, or NoSuchProjectDatabase (and the alias is forgotten)."""
    try:
        connections[alias].ensure_connection()
    except OperationalError as exc:
        if _is_gone(exc):
            forget(alias)
            raise NoSuchProjectDatabase(alias) from None
        raise


def _lock_of(alias: str) -> threading.Lock:
    with _migrate_locks_lock:
        return _migrate_locks.setdefault(alias, threading.Lock())


def open_alias(alias: str) -> str:
    """Connect, and migrate the first time this process opens the database."""
    ensure(alias)
    if alias in _migrated:
        return alias
    with _lock_of(alias):
        if alias in _migrated:
            return alias
        from aisc_backend.management.commands.migrate_projects import migrate_database

        try:
            migrate_database(alias)
        except (NoSuchProjectDatabase, SchemaNotProvisioned):
            raise
        except Exception as exc:
            raise MigrationFailed(f"{alias}: {type(exc).__name__}") from exc
        _migrated.add(alias)
    return alias


class ProjectDatabaseRouter:
    """Every model to the admitted project database; nothing to default or platform."""

    def _admitted(self):
        alias = admitted.get()
        if not alias:
            raise NoProjectAdmitted("no project database is admitted for this query")
        return alias

    def db_for_read(self, model, **hints):
        return self._admitted()

    def db_for_write(self, model, **hints):
        return self._admitted()

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, **hints):
        return bool(DATABASE.match(db or ""))
