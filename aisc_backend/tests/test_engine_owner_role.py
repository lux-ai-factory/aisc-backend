"""The engine's tables in a project database belong to engine_owner, a role no application logs in as. The
runtime role engine_rw keeps DML only: it cannot truncate, create tables or lift a guard. A run's card version
is fixed when the run starts, and a card version a run names cannot be deleted from under it. A migration on a
database whose engine schema has no owner role stops and names postgres-setup.

Runs in the engine suite's --postgres mode (superuser_url, a throwaway Postgres); without it every case skips.
"""
from __future__ import annotations

import os
import unittest

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, configurator_only, database_name
from aisc_backend.tests.project_database_world import World, manage, orm

SKIP_REASON = f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it (engine suite)"
V1 = "11111111-1111-4111-8111-111111111111"
V2 = "22222222-2222-4222-8222-222222222222"

_WORLD: World | None = None


def world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World("engine owner")
        with _WORLD.connect() as conn:
            for number, pid in ((1, V1), (2, V2)):
                conn.execute("INSERT INTO project.system (pid, number, name) VALUES (%s, %s, %s)",
                             (pid, number, f"card {number}"))
    return _WORLD


def tearDownModule():
    if _WORLD is not None:
        _WORLD.cleanup()


def _evaluation(w: World, system_id: str | None) -> int:
    from aisc_backend.models import Evaluation, EvaluationStatus, Project

    with orm(w.pid):
        project, _ = Project.objects.get_or_create(platform_project_id=w.pid,
                                                   defaults={"name": "engine owner", "description": ""})
        return Evaluation.objects.create(status=EvaluationStatus.Processing, project=project,
                                         system_id=system_id).id


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class EngineOwnerRole(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def test_engine_tables_belong_to_engine_owner(self):
        with self.w.connect() as conn:
            owners = {r[0] for r in conn.execute(
                "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'engine'").fetchall()}
        self.assertEqual(owners, {"engine_owner"})

    def test_the_engine_schema_belongs_to_engine_owner(self):
        self.assertEqual(self.w.scalar("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'engine'"),
                         "engine_owner")

    def test_the_runtime_role_cannot_truncate_a_table(self):
        error = self.w.refusal("TRUNCATE engine.aisc_backend_measurement CASCADE", role="engine_rw")
        self.assertIsNotNone(error, "engine_rw truncated the measurements")
        self.assertIn("InsufficientPrivilege", error)

    def test_the_runtime_role_cannot_create_a_table_in_the_engine_schema(self):
        error = self.w.refusal("CREATE TABLE engine.made_by_the_runtime_role (id int)", role="engine_rw")
        self.assertIsNotNone(error, "engine_rw created a table in schema engine")
        self.assertIn("InsufficientPrivilege", error)

    def test_the_runtime_role_cannot_lift_a_trigger(self):
        error = self.w.refusal("ALTER TABLE engine.aisc_backend_measurement DISABLE TRIGGER USER", role="engine_rw")
        self.assertIsNotNone(error, "engine_rw disabled the triggers of its own table")
        self.assertIn("must be owner", error)

    def test_the_runtime_role_is_not_a_member_of_engine_owner(self):
        self.assertFalse(self.w.scalar(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_owner')"
            " AND pg_has_role('engine_rw', 'engine_owner', 'MEMBER')"))
        self.assertTrue(self.w.scalar("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_owner')"),
                        "role engine_owner does not exist")

    def test_a_run_keeps_its_card_version(self):
        run = _evaluation(self.w, V1)
        error = self.w.refusal("UPDATE engine.aisc_backend_evaluation SET system_id = %s WHERE id = %s",
                               role="engine_rw", params=(V2, run))
        self.assertIsNotNone(error, "a run's card version was changed after it started")
        self.assertIn("fixed at its start", error)

    def test_a_run_started_without_a_card_version_cannot_be_given_one_later(self):
        run = _evaluation(self.w, None)
        error = self.w.refusal("UPDATE engine.aisc_backend_evaluation SET system_id = %s WHERE id = %s",
                               role="engine_rw", params=(V1, run))
        self.assertIsNotNone(error, "a card version was set on a run after it started")
        self.assertIn("fixed at its start", error)

    def test_the_card_version_reference_restricts(self):
        self.assertEqual(self.w.scalar(
            "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'engine.aisc_backend_evaluation'::regclass"
            " AND confrelid = 'project.system'::regclass"), "r")

    def test_the_platform_reads_only_the_card_version_and_pid_of_a_run(self):
        self.assertTrue(self.w.scalar(
            "SELECT has_column_privilege('platform_rw', 'engine.aisc_backend_evaluation', 'system_id', 'SELECT')"),
            "platform_rw cannot read which card version a run used")

    def test_module_migration_refuses_without_owner_role(self):
        """A project database whose engine schema was not handed to engine_owner (postgres-setup not run): the
        migration stops and names postgres-setup instead of making tables the runtime role owns."""
        w = self.w
        pid = w.cluster.platform_project("engine no owner", {"alice": "owner"})
        w.cluster.provision(pid)
        db = database_name(pid)
        try:
            with w.cluster.connect(db) as conn:
                conn.execute("ALTER SCHEMA engine OWNER TO platform_rw")
            done = manage("migrate_projects")
            self.assertNotEqual(done.returncode, 0, "the migration ran on a database without the engine's owner role")
            self.assertIn("postgres-setup", done.stdout + done.stderr)
        finally:
            w.cluster.drop_database(db)

