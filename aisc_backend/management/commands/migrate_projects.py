"""`manage.py migrate_projects`: the engine's tables in every project database (I7.6).

The one-shot `aisc-backend-migrate` runs this at start; the engine also runs
`migrate_database` the first time a process opens a project database
(projectdb.open_alias). For each `project_<hex>` database the engine's role may
connect to, it applies the Django migrations (schema `engine`, search_path set
by the alias) and then grants the readers what I2.6 lists, as the owner of the
tables. Both happen under a Postgres advisory lock taken in that database, so two
runs at once migrate it once.

The database list comes from pg_database, not from the platform's project
table: a database the role may not enter (no template yet, or an orphan) is
skipped, and a dropped one is simply absent. Output names databases and
exception types only, never a DSN or a row.
"""
from __future__ import annotations

import os
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connections

from aisc_backend import projectdb

#: plan 4's key ("engine" in ASCII); advisory locks are per database
ENGINE_MIGRATE_LOCK = 0x656E67696E65

#: I2.6, the engine row: what report_ro and dashboard_ro may read. Nothing on
#: aisc_backend_projectconfig, aisc_backend_pluginconfigprojectconfig,
#: aisc_backend_pluginconfig.config or the bookkeeping tables, and no default
#: privileges. The tables keep Sean's names (aisc_backend_<model>).
READER_GRANTS = """
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['report_ro','dashboard_ro'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT SELECT ON engine.aisc_backend_project, engine.aisc_backend_aisystem,
        engine.aisc_backend_aicomponent, engine.aisc_backend_evaluation, engine.aisc_backend_evaluationplugin,
        engine.aisc_backend_evaluationinput, engine.aisc_backend_plugin, engine.aisc_backend_observation,
        engine.aisc_backend_measurement, engine.aisc_backend_metric, engine.aisc_backend_direct,
        engine.aisc_backend_derived, engine.aisc_backend_metriccategory, engine.aisc_backend_metriccategory_metrics,
        engine.aisc_backend_artifact TO %I', r);
      EXECUTE format('GRANT SELECT (id, plugin_id, name) ON engine.aisc_backend_pluginconfig TO %I', r);
    END IF;
  END LOOP;
END $$;
"""

LIST_DATABASES = """
SELECT datname FROM pg_database
WHERE datname ~ '^project_[0-9a-f]{32}$'
  AND has_database_privilege(current_user, datname, 'CONNECT')
ORDER BY datname
"""


def grant_readers(connection) -> None:
    """I2.6 (engine row), issued by the table owner; not a Django migration."""
    with connection.cursor() as cursor:
        cursor.execute(READER_GRANTS)


def _provisioned(connection) -> bool:
    """The engine schema exists and the role may create tables in it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT oid FROM pg_namespace WHERE nspname = 'engine'")
        row = cursor.fetchone()
        if row is None:
            return False
        cursor.execute("SELECT has_schema_privilege(current_user, 'engine', 'CREATE')")
        return bool(cursor.fetchone()[0])


def migrate_database(alias: str) -> None:
    """Migrate one project database and grant its readers, under its advisory lock."""
    token = projectdb.admitted.set(alias)  # historical-model RunPython queries route here
    try:
        projectdb.ensure(alias)
        connection = connections[alias]
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('django_migrations') IS NOT NULL")
            has_history = cursor.fetchone()[0]
        if not has_history and not _provisioned(connection):
            raise projectdb.SchemaNotProvisioned(alias)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [ENGINE_MIGRATE_LOCK])
        try:
            call_command("migrate", database=alias, interactive=False, verbosity=0)
            grant_readers(connection)
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [ENGINE_MIGRATE_LOCK])
            except Exception:  # noqa: BLE001 - a broken session releases the lock itself
                connection.close()
    finally:
        projectdb.admitted.reset(token)


class Command(BaseCommand):
    help = "Migrate the engine's tables in every project database the engine may connect to."

    def _wait_for_platform(self, platform) -> None:
        wait = float(os.environ.get("MIGRATE_PROJECTS_WAIT", "60"))
        deadline = time.monotonic() + wait
        while True:
            try:
                platform.ensure_connection()
                return
            except OperationalError:
                platform.close()
                if time.monotonic() >= deadline:
                    raise CommandError("the platform database did not answer") from None
                time.sleep(2)

    def handle(self, *args, **options):
        if not projectdb.enabled():
            raise CommandError("migrate_projects needs the project databases (DB_ENGINE postgresql)")
        platform = connections["platform"]
        self._wait_for_platform(platform)
        with platform.cursor() as cursor:
            cursor.execute(LIST_DATABASES)
            names = [row[0] for row in cursor.fetchall()]
        platform.close()

        failed = []
        for name in names:
            alias = projectdb.alias_for(projectdb.pid_of(name))
            try:
                migrate_database(alias)
                self.stdout.write(f"migrated {name}")
            except (projectdb.NoSuchProjectDatabase, projectdb.SchemaNotProvisioned):
                self.stdout.write(f"skipped {name}")
            except Exception as exc:  # noqa: BLE001 - reported by type only, then the next one
                failed.append(name)
                self.stderr.write(f"failed {name}: {type(exc).__name__}")
            finally:
                try:
                    connections[alias].close()
                except Exception:  # noqa: BLE001
                    pass
        if failed:
            raise CommandError(f"{len(failed)} project database(s) failed to migrate")
