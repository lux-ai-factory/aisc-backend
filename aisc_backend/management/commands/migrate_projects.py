"""`manage.py migrate_projects`: the engine's tables in every project database.

The one-shot `aisc-backend-migrate` runs this at start; the engine also runs
`migrate_database` the first time a process opens a project database
(projectdb.open_alias). For each `project_<hex>` database the engine's role may
connect to, it applies the Django migrations (schema `engine`, search_path set
by the alias) and then grants the readers what READER_GRANTS lists, as the owner of the
tables. Both run on the database's migration alias (projectdb.migrate_alias: engine_migrator,
acting as engine_owner), and only once the database's engine schema is engine_owner's: a schema
still the platform's waits for the platform to hand it over (postgres-setup makes the role), and
nothing is migrated. Both happen under a Postgres advisory lock taken in that database, so two
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

#: The lock key ("engine" in ASCII); advisory locks are per database
ENGINE_MIGRATE_LOCK = 0x656E67696E65

#: What report_ro and dashboard_ro may read. Nothing on
#: aisc_backend_projectconfig, aisc_backend_pluginconfigprojectconfig,
#: aisc_backend_pluginconfig.config or the bookkeeping tables, and no default
#: privileges. The tables keep the upstream names (aisc_backend_<model>).
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
  -- The platform drops a project database only once this outbox is sent: it counts the rows left
  -- (which, not what they say). It owns the schema, so it may already use it.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_rw') THEN
    GRANT SELECT (id, delivered_at, refused) ON engine.aisc_backend_ledgeroutbox TO platform_rw;
  END IF;
END $$;
"""

LIST_DATABASES = """
SELECT datname FROM pg_database
WHERE datname ~ '^project_[0-9a-f]{32}$'
  AND has_database_privilege(current_user, datname, 'CONNECT')
ORDER BY datname
"""


def grant_readers(connection) -> None:
    """The readers' grants, issued by the table owner; not a Django migration."""
    with connection.cursor() as cursor:
        cursor.execute(READER_GRANTS)


def _schema_owner(connection) -> str | None:
    """Who owns the engine schema of the database, or None when it is not there (no template yet)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_userbyid(nspowner)::text FROM pg_namespace WHERE nspname = 'engine'")
        row = cursor.fetchone()
    return row[0] if row else None


def migrate_database(alias: str) -> None:
    """Migrate one project database and grant its readers, as the migrator, under its advisory lock."""
    projectdb.ensure(alias)
    migrator = projectdb.migrate_alias(alias)
    token = projectdb.admitted.set(migrator)  # historical-model RunPython queries route here
    try:
        projectdb.ensure(migrator)
        connection = connections[migrator]
        owner = _schema_owner(connection)
        if owner is None:
            raise projectdb.SchemaNotProvisioned(alias)
        if owner != projectdb.SCHEMA_OWNER:
            raise projectdb.SchemaNotOwned(alias, owner)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [ENGINE_MIGRATE_LOCK])
        try:
            call_command("migrate", database=migrator, interactive=False, verbosity=0)
            grant_readers(connection)
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [ENGINE_MIGRATE_LOCK])
            except Exception:  # noqa: BLE001 - a broken session releases the lock itself
                connection.close()
    finally:
        projectdb.admitted.reset(token)
        connections[migrator].close()


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
            except projectdb.SchemaNotOwned as exc:
                failed.append(name)
                self.stderr.write(f"{'waiting' if exc.wait else 'failed'} {exc}")
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
