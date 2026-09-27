"""Where this engine runs: on its own, or inside the Sandbox Configurator.

One switch, AISC_DEPLOYMENT, read once. Every behaviour that differs between the two asks this
module (docs/superpowers/specs/2026-09-27-engine-deployment-modes.md has the list); nothing else
reads the variable.
"""
import os
from collections.abc import Callable, Mapping

from django.core.exceptions import ImproperlyConfigured

STANDALONE = "standalone"
CONFIGURATOR = "configurator"
MODES = (STANDALONE, CONFIGURATOR)
_SETTINGS_NAMES = ("AISC_DEPLOYMENT", "DB_ENGINE")


def settings_source(read: Callable[[str], str | None]) -> dict[str, str]:
    """Collect AISC_DEPLOYMENT/DB_ENGINE through `read`, whatever loaded them (os.environ directly,
    or environs.Env after it has read a .env file, which never writes back into os.environ). Names
    `read` has nothing for are left out, so mode()/check_environment() still default to standalone."""
    return {name: value for name in _SETTINGS_NAMES if (value := read(name)) is not None}


def mode(env: Mapping[str, str] = os.environ) -> str:
    if "AISC_DEPLOYMENT" not in env:
        return STANDALONE
    value = env["AISC_DEPLOYMENT"].strip().lower()
    if value not in MODES:
        raise ImproperlyConfigured(
            f"AISC_DEPLOYMENT must be {STANDALONE} or {CONFIGURATOR}, not {env['AISC_DEPLOYMENT']!r}")
    return value


def _postgres(env: Mapping[str, str]) -> bool:
    return "postgresql" in env.get("DB_ENGINE", "django.db.backends.sqlite3")


def project_databases(env: Mapping[str, str] = os.environ) -> bool:
    """One database per project: the Configurator, on Postgres. Standalone is always one database."""
    return mode(env) == CONFIGURATOR and _postgres(env)


def check_environment(env: Mapping[str, str] = os.environ, testing: bool | None = None) -> None:
    """The Configurator makes a database per project, which takes Postgres. The test runner alone may
    run it on one sqlite database (the configurator unit tests do, as they did before the modes)."""
    if testing is None:
        import sys
        testing = sys.argv[1:2] == ["test"]
    if mode(env) == CONFIGURATOR and not _postgres(env) and not testing:
        raise ImproperlyConfigured(
            f"AISC_DEPLOYMENT is {CONFIGURATOR}: DB_ENGINE must be django.db.backends.postgresql "
            "(one database per project)")


def is_configurator() -> bool:
    from django.conf import settings
    return settings.AISC_DEPLOYMENT == CONFIGURATOR


def is_standalone() -> bool:
    return not is_configurator()


MARKER_TABLE = "engine_deployment"


def made_in(connection) -> str | None:
    """The mode this database was made in (migration 0025 writes it), or None when the database
    has no marker yet (not migrated so far, or the row was removed)."""
    if MARKER_TABLE not in connection.introspection.table_names():
        return None
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT mode FROM {connection.ops.quote_name(MARKER_TABLE)}")
        row = cursor.fetchone()
    return row[0] if row else None


def assert_database_mode(connection) -> None:
    """A database made by one mode is never run by the other: their schemas differ (0023 drops
    the login tables in configurator only), so the engine stops instead of running on it."""
    from django.conf import settings
    made, now = made_in(connection), settings.AISC_DEPLOYMENT
    if made is not None and made != now:
        raise ImproperlyConfigured(f"this database was made by a {made} engine; this engine is {now}")


#: What `before_migrate` found in a database that predates 0025, by alias, for 0025 to stamp.
_found_before_migrate: dict[str, str] = {}


def infer_made_in(connection) -> str | None:
    """The mode of a database that predates the marker, or None for a fresh one (no aisc_backend
    migration recorded). 0023 drops the login tables in configurator only, so a database that has
    `auth_user` was made standalone, and one that lost it was made by a configurator engine."""
    tables = set(connection.introspection.table_names())
    if "django_migrations" not in tables:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_migrations WHERE app = %s", ["aisc_backend"])
        if not cursor.fetchone()[0]:
            return None
    return STANDALONE if "auth_user" in tables else CONFIGURATOR


def before_migrate(sender, using, **kwargs) -> None:
    """pre_migrate receiver: refuse a migrate of the other mode before it changes anything (it would
    remake or drop the login tables), and remember what a pre-0025 database was for 0025's stamp.
    Runs before any migration of the run, so the login tables are still as the database had them."""
    if getattr(sender, "name", None) != "aisc_backend":
        return
    from django.conf import settings
    from django.db import connections

    connection = connections[using]
    made = made_in(connection)
    if made is None:
        made = infer_made_in(connection)
        if made is not None:
            _found_before_migrate[using] = made
    if made is not None and made != settings.AISC_DEPLOYMENT:
        raise ImproperlyConfigured(
            f"this database was made by a {made} engine; this engine is {settings.AISC_DEPLOYMENT}")


def stamp_for(connection) -> str:
    """The mode 0025 writes: what the database was before this migrate, else (a fresh database,
    or migrations run without the pre_migrate signal) the running mode."""
    from django.conf import settings
    return _found_before_migrate.pop(connection.alias, None) or settings.AISC_DEPLOYMENT
