"""One throwaway project database for the engine's role and guard tests: a platform project, its database
provisioned with the real template, the engine's migrations applied by `manage.py migrate_projects`, as a live
install makes it. Not a test module.

Needs the throwaway Postgres of the engine suite's --postgres mode (ENGINE_TEST_SUPERUSER_URL and the engine's
DB_* pointed at it); never the running stack.
"""
from __future__ import annotations

import os
import subprocess
import sys

from django.conf import settings

from aisc_backend.tests.isolation_support import BACKEND, Cluster, database_name, dsn_for, superuser_url


def manage(*args) -> subprocess.CompletedProcess:
    """`manage.py <args>` in a child process with this process's (deployed) environment."""
    env = dict(os.environ)
    env.setdefault("RUN_TICKET_KEY", settings.RUN_TICKET_KEY)
    env.setdefault("AUTH_ENABLED", "true")
    return subprocess.run([sys.executable, "manage.py", *args], cwd=BACKEND, env=env, capture_output=True,
                          text=True, timeout=600)


class World:
    def __init__(self, label: str):
        self.cluster = Cluster(superuser_url())
        self.pid = self.cluster.platform_project(label, {"alice": "owner"})
        self.cluster.provision(self.pid)
        migrated = manage("migrate_projects")
        if migrated.returncode != 0:
            raise AssertionError(f"manage.py migrate_projects exited {migrated.returncode}: "
                                 + (migrated.stderr.strip().splitlines() or ["(no output)"])[-1][:300])
        self.database = database_name(self.pid)

    def connect(self, role: str | None = None, autocommit: bool = True):
        """A connection to the project database, as the superuser or as `role` (password: its name)."""
        import psycopg

        return psycopg.connect(dsn_for(self.cluster.url, self.database, user=role), autocommit=autocommit)

    def scalar(self, query: str, params=()):
        with self.connect() as conn:
            return conn.execute(query, params).fetchone()[0]

    def refusal(self, query: str, role: str | None = None, set_role: str | None = None, params=()) -> str | None:
        """Run `query` in a transaction that is rolled back; the error it raised, or None when it went through."""
        import psycopg

        with self.connect(role, autocommit=False) as conn:
            try:
                if set_role:
                    conn.execute(f'SET ROLE "{set_role}"')
                conn.execute(query, params)
                return None
            except psycopg.Error as exc:
                return f"{type(exc).__name__}: {exc}"
            finally:
                conn.rollback()

    def owner_of(self, table: str) -> str:
        return self.scalar("SELECT tableowner FROM pg_tables WHERE schemaname = 'engine' AND tablename = %s",
                           (table,))

    def cleanup(self):
        self.cluster.cleanup()


def orm(pid: str):
    """The project's alias admitted, as the engine writes: use as `with orm(pid) as alias:`."""
    from contextlib import contextmanager

    from django.db import connections

    from aisc_backend import projectdb

    @contextmanager
    def admitted():
        alias = projectdb.open_alias(projectdb.alias_for(pid))
        token = projectdb.admitted.set(alias)
        try:
            yield alias
        finally:
            projectdb.admitted.reset(token)
            connections[alias].close()

    return admitted()

