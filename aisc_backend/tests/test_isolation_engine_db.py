"""Isolation 2026-09-25, the engine on project databases: the Postgres half
(01-specs.md section 7; the database-free half is test_isolation_engine.py).

Needs a throwaway Postgres (never the running stack) with init/platform-db.sql,
init/project-databases.sql, init/report-roles.sql and init/inspector-role.sql
applied, and the engine's deployed environment pointed at it:

    ENGINE_TEST_SUPERUSER_URL=postgresql://<superuser>:<pw>@127.0.0.1:<port>/platform
    PLATFORM_TEST_DATABASE_URL=postgresql://platform_rw:platform_rw@127.0.0.1:<port>/platform
    DB_ENGINE=django.db.backends.postgresql DB_NAME=platform DB_USER=engine_rw
    DB_PASSWORD=engine_rw DB_HOST=127.0.0.1 DB_PORT=<port> DB_SCHEMA=engine
    .venv/bin/python manage.py test aisc_backend.tests.test_isolation_engine_db

(isolation 02-tests.md has the full recipe). Without ENGINE_TEST_SUPERUSER_URL
every test here skips, so the sqlite unit run is unaffected.

These are plain unittest cases, not Django's TestCase: Django's runner then
creates no test database, and the tests use the real project databases the
platform makes (`projectdb.provision`, so they get the real template).

What is built once for the module (`world()`): three platform projects A, B, C,
their databases, card versions in `project.system` (A has numbers 1 and 2, B has
1), `manage.py migrate_projects` run over them, and in A and B one of each
engine object (engine project, AI system, dataset and model components, a
project config, a plugin with its config, an evaluation with a task id, its
evaluation plugin, an artifact, an observation, a metric and a measurement),
written through the ORM with that project's alias admitted. alice is owner of
A, B and C; bob is a viewer of A. So every cross-project refusal below is the
database's doing, not the membership check's: alice may see both projects.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
import uuid

from aisc_backend.tests.isolation_support import (
    configurator_only,
    BACKEND, LIVE_SHAPE, SUPERUSER_URL_VAR, ApiCaller, Cluster, catalog, database_name, superuser_url,
)

SKIP_REASON = (f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it "
               "(isolation 02-tests.md, engine suite)")


def _url():
    return superuser_url()


_WORLD: dict | None = None
_WORLD_ERROR: str | None = None
#: every Cluster made here, cleaned up by tearDownModule even when the build failed
_CLUSTERS: list = []


def _migrate_projects(extra_env=None) -> subprocess.CompletedProcess:
    """`manage.py migrate_projects` in a child process with this process's
    (deployed) environment. Output is captured and never printed."""
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "manage.py", "migrate_projects"], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=600)


def _seed(pid: str, label: str) -> dict:
    """One of each engine object in this project's database, through the ORM."""
    from django.utils import timezone

    from aisc_backend import projectdb
    from aisc_backend.models import (
        AIComponent, AISystem, Direct, Evaluation, EvaluationPlugin, Measurement, Observation, Plugin,
        PluginConfig, Project, ProjectConfig,
    )
    from aisc_backend.models.ai_system import AIComponentType
    from aisc_backend.models.artifact import Artifact
    from aisc_backend.models.common import StorageContainer
    from aisc_backend.models.evaluation import EvaluationStatus
    from aisc_backend.models.project import ProjectStatus

    alias = projectdb.alias_for(pid)
    token = projectdb.admitted.set(alias)
    try:
        project = Project.objects.create(name=f"{label} workspace {pid[:8]}", description="",
                                         status=ProjectStatus.Created, platform_project_id=pid)
        system = AISystem.objects.create(project=project, name=f"{label} system", description="")
        dataset = AIComponent.objects.create(system=system, name="train", description="",
                                             component_type=AIComponentType.DATASET,
                                             data=f"{uuid.uuid4()}.csv",
                                             storage_container=StorageContainer.Datasets)
        model = AIComponent.objects.create(system=system, name="clf", description="",
                                           component_type=AIComponentType.MODEL,
                                           data=f"{uuid.uuid4()}.onnx",
                                           storage_container=StorageContainer.Models)
        config = ProjectConfig.objects.create(project=project, name="threshold", description="",
                                              key="threshold", category="variables",
                                              json_value={"value": 1})
        plugin = Plugin.objects.create(project=project, name="probe", description="",
                                       package_name="aisc-probe", version="1.0.0",
                                       display_name="Probe", enabled=True)
        plugin_config = PluginConfig.objects.create(plugin=plugin, name="probe config", description="",
                                                    config={})
        plugin.current_config = plugin_config
        plugin.save()
        evaluation = Evaluation.objects.create(project=project, status=EvaluationStatus.Done,
                                               task=uuid.uuid4())
        run = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=plugin_config,
                                              name="probe", description="")
        artifact = Artifact.objects.create(evaluation_plugin=run, name="log.txt", description="",
                                           data=f"{uuid.uuid4()}.txt",
                                           storage_container=StorageContainer.Artifacts)
        observation = Observation.objects.create(evaluation=evaluation, name="obs", description="",
                                                 observer="probe", tool="probe")
        metric = Direct.objects.create(name="accuracy", description="", type_spec="float")
        Measurement.objects.create(observation=observation, metric=metric, name="accuracy",
                                   description="", time=timezone.now(), score=0.9)
        return {
            "platform": pid, "database": alias, "pid": str(project.pid), "name": project.name,
            "component_pid": str(dataset.pid), "model_pid": str(model.pid),
            "dataset_file": dataset.data, "model_file": model.data, "artifact_file": artifact.data,
            "project_config_pid": str(config.pid), "plugin_pid": str(plugin.pid),
            "config_id": plugin_config.id, "evaluation_pid": str(evaluation.pid),
            "evaluation_plugin_pid": str(run.pid), "task": str(evaluation.task),
            "system_id": str(evaluation.system_id) if evaluation.system_id else None,
        }
    finally:
        projectdb.admitted.reset(token)
        from django.db import connections

        connections[alias].close()


def _build() -> dict:
    cluster = Cluster(_url())
    _CLUSTERS.append(cluster)
    a = cluster.platform_project("A", {"alice": "owner", "bob": "viewer"})
    b = cluster.platform_project("B", {"alice": "owner"})
    c = cluster.platform_project("C", {"alice": "owner"})
    for pid in (a, b, c):
        cluster.provision(pid)
    versions = {}
    for pid, numbers in ((a, (1, 2)), (b, (1,)), (c, (1,))):
        with cluster.connect(database_name(pid)) as conn:
            if conn.execute("SELECT to_regclass('project.system')").fetchone()[0] is None:
                raise AssertionError("I1.5/I2.1: project.system is missing from the project database "
                                     "(template 0006_project_system.sql, WP P1)")
            versions[pid] = [conn.execute(
                "INSERT INTO project.system (number, name) VALUES (%s, %s) RETURNING pid::text",
                (n, f"card v{n}")).fetchone()[0] for n in numbers]
    migrated = _migrate_projects()
    if migrated.returncode != 0:
        raise AssertionError(f"I7.6: manage.py migrate_projects exited {migrated.returncode}: "
                             + (migrated.stderr.strip().splitlines() or ["(no output)"])[-1][:300])
    return {"cluster": cluster, "A": _seed(a, "A"), "B": _seed(b, "B"), "C": c,
            "versions": versions, "migrated": migrated}


def world(test: unittest.TestCase) -> dict:
    global _WORLD, _WORLD_ERROR
    if _WORLD_ERROR is not None:
        test.fail(_WORLD_ERROR)
    if _WORLD is None:
        try:
            _WORLD = _build()
        except AssertionError as exc:
            _WORLD_ERROR = str(exc)
            test.fail(_WORLD_ERROR)
        except Exception as exc:  # noqa: BLE001 - reported as the reason every test fails
            _WORLD_ERROR = f"building the two-project world failed: {type(exc).__name__}: {str(exc)[:300]}"
            test.fail(_WORLD_ERROR)
    return _WORLD


def tearDownModule():
    for cluster in _CLUSTERS:
        cluster.cleanup()


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class PostgresCase(unittest.TestCase):
    def setUp(self):
        self.w = world(self)
        self.cluster: Cluster = self.w["cluster"]
        self.api = ApiCaller()
        self.addCleanup(self.api.stop)


# ── I7.6, I7.7, I2.6 (engine part): migrate_projects ──


@configurator_only
class TheOneShotMigratesEveryProjectDatabase(PostgresCase):
    def test_i7_6_every_project_database_is_at_0021(self):
        for key in ("A", "B"):
            with self.subTest(project=key):
                names = {r[0] for r in self.cluster.rows(
                    self.w[key]["database"], "SELECT name FROM engine.django_migrations WHERE app = 'aisc_backend'")}
                # renumbered on feat/deployment-modes (adapt item 1), with the mode marker
                self.assertIn("0021_engine_deployment_marker", names)
                self.assertIn("0020_the_database_is_the_project", names)
                self.assertIn("0019_no_login_of_its_own", names)

    def test_i7_6_the_tables_belong_to_engine_rw_in_schema_engine(self):
        owners = {r[0] for r in self.cluster.rows(
            self.w["A"]["database"],
            "SELECT tableowner FROM pg_tables WHERE schemaname = 'engine'")}
        self.assertEqual(owners, {"engine_rw"})
        public = self.cluster.rows(self.w["A"]["database"],
                                   "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")[0][0]
        self.assertEqual(public, 0, "I2.1: search_path=engine, nothing lands in public")

    def test_i7_6_nothing_is_migrated_into_platform(self):
        count = self.cluster.rows(None, "SELECT count(*) FROM pg_tables WHERE schemaname = 'engine'")[0][0]
        self.assertEqual(count, 0, "I7.6: aisc-backend no longer migrates platform")

    def test_i7_6_a_database_it_may_not_enter_is_skipped(self):
        bare = self.cluster.bare_database()
        again = _migrate_projects()
        self.assertEqual(again.returncode, 0, "I7.6: a database engine_rw may not enter is skipped, not fatal")
        schemas = self.cluster.rows(bare, "SELECT count(*) FROM pg_namespace WHERE nspname = 'engine'")[0][0]
        self.assertEqual(schemas, 0)

    def test_i7_6_it_is_idempotent_and_two_at_once_migrate_once(self):
        pid = self.cluster.platform_project("D", {"alice": "owner"})
        self.cluster.provision(pid)
        env = dict(os.environ)
        runs = [subprocess.Popen([sys.executable, "manage.py", "migrate_projects"], cwd=BACKEND, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
        codes = [r.wait(timeout=600) for r in runs]
        self.assertEqual(codes, [0, 0], "I7.6: concurrent runs wait on the per-database advisory lock")
        dupes = self.cluster.rows(database_name(pid),
                                  "SELECT name FROM engine.django_migrations GROUP BY app, name HAVING count(*) > 1")
        self.assertEqual(dupes, [])

    def test_i7_7_the_evaluation_points_at_project_system_set_null(self):
        rows = self.cluster.rows(self.w["A"]["database"], """
            SELECT confrelid::regclass::text, confdeltype FROM pg_constraint
            WHERE conname = 'aisc_backend_evaluation_system_id_fkey'""")
        self.assertEqual(rows, [("project.system", "n")])

    def test_i7_9_no_foreign_key_leaves_the_database(self):
        rows = self.cluster.rows(self.w["A"]["database"], """
            SELECT conname FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
            WHERE n.nspname = 'engine' AND k.contype = 'f'
              AND k.confrelid::regclass::text LIKE 'core.%%'""")
        self.assertEqual(rows, [], "I7.9: no key to core (there is no core in a project database)")


@configurator_only
class TheEngineReaderGrants(PostgresCase):
    """I2.6 engine row: issued by migrate_projects as engine_rw (I7.6)."""

    READABLE = ["aisc_backend_project", "aisc_backend_aisystem", "aisc_backend_aicomponent",
                "aisc_backend_evaluation", "aisc_backend_evaluationplugin", "aisc_backend_evaluationinput",
                "aisc_backend_plugin", "aisc_backend_observation", "aisc_backend_measurement",
                "aisc_backend_metric", "aisc_backend_direct", "aisc_backend_derived",
                "aisc_backend_metriccategory", "aisc_backend_metriccategory_metrics", "aisc_backend_artifact"]
    SECRET = ["aisc_backend_projectconfig", "aisc_backend_pluginconfigprojectconfig", "django_migrations",
              "django_content_type"]

    def _can(self, role, table, privilege="SELECT"):
        return self.cluster.rows(self.w["A"]["database"],
                                 "SELECT has_table_privilege(%s, %s, %s)", (role, f"engine.{table}", privilege))[0][0]

    def test_i2_6_readers_see_the_list(self):
        for role in ("report_ro", "dashboard_ro"):
            for table in self.READABLE:
                with self.subTest(role=role, table=table):
                    self.assertTrue(self._can(role, table))

    def test_i2_6_readers_never_see_secrets_or_bookkeeping(self):
        for role in ("report_ro", "dashboard_ro"):
            for table in self.SECRET:
                with self.subTest(role=role, table=table):
                    self.assertFalse(self._can(role, table))

    def test_i2_6_plugin_config_by_column(self):
        for role in ("report_ro", "dashboard_ro"):
            with self.subTest(role=role):
                self.assertFalse(self._can(role, "aisc_backend_pluginconfig"), "no table-wide SELECT")
                for column, allowed in (("id", True), ("plugin_id", True), ("config", False)):
                    got = self.cluster.rows(self.w["A"]["database"],
                                            "SELECT has_column_privilege(%s, 'engine.aisc_backend_pluginconfig', %s, 'SELECT')",
                                            (role, column))[0][0]
                    self.assertEqual(got, allowed, f"plugin_config.{column}")

    def test_i2_6_readers_write_nothing(self):
        for role in ("report_ro", "dashboard_ro"):
            for table in self.READABLE:
                with self.subTest(role=role, table=table):
                    self.assertFalse(self._can(role, table, "INSERT"))


# ── I7.9: the table definitions do not change ──


@configurator_only
class TheShapeIsTheLiveShape(PostgresCase):
    """Catalog diff between A's `engine` schema (migrated by I7.6) and the live
    `platform.engine` shape (scripts/tests/fixtures/isolation/live_shape.sql,
    schema only). The two allowed differences are applied to the live side
    before comparing: the FK from project.project_id to core.project is gone,
    and evaluation.system_id references project.system instead of core.system."""

    ALLOWED_GONE = {"aisc_backend_project_platform_project_id_fkey"}

    #: Ruling 14: three foreign keys keep their columns and targets but take the names Sean's
    #: squashed 0014 gives them (the live shape was made under the old history). Live name to
    #: the name from now on.
    RENAMED = {
        "aisc_backend_aisystem_project_id_381e09a9_fk_project_id":
            "aisc_backend_aisyste_project_id_381e09a9_fk_aisc_back",
        "aisc_backend_evaluat_evaluation_plugin_id_7c4716b3_fk_evaluatio":
            "aisc_backend_evaluat_evaluation_plugin_id_7c4716b3_fk_aisc_back",
        "plugin_config_settin_project_config_id_505b7676_fk_project_s":
            "aisc_backend_pluginc_project_config_id_d1306b7a_fk_aisc_back",
    }

    def _live(self):
        scratch = self.cluster.scratch_database("iso_eng_live_shape")
        with self.cluster.connect(scratch) as conn:
            conn.execute("CREATE SCHEMA core")
            # the fixture keeps core.system's trigger but not its function (core is not a moving schema)
            conn.execute("CREATE FUNCTION core.system_only_latest_changes() RETURNS trigger "
                         "LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'")
            conn.execute(LIVE_SHAPE.read_text())
            return catalog(conn, "engine")

    def test_i7_9_every_table_column_index_and_key_is_as_live(self):
        live = self._live()
        with self.cluster.connect(self.w["A"]["database"]) as conn:
            mine = catalog(conn, "engine")
        # The one table the live shape predates: the mode marker (0021_engine_deployment_marker).
        mine = {kind: {r for r in rows if r[0] != "engine_deployment"} for kind, rows in mine.items()}
        live["constraints"] = {
            (t, self.RENAMED.get(name, name), kind,
             d.replace("REFERENCES core.system(pid)", "REFERENCES project.system(pid)"))
            for t, name, kind, d in live["constraints"] if name not in self.ALLOWED_GONE}
        for kind in live:
            with self.subTest(kind=kind):
                self.assertEqual(sorted(mine[kind] - live[kind]), [], f"I7.9: {kind} only in the project database")
                self.assertEqual(sorted(live[kind] - mine[kind]), [], f"I7.9: {kind} only in the live shape")


# ── I7.1, I7.8, I7.10: the ORM goes to the admitted database ──


@configurator_only
class TheOrmStaysInItsDatabase(PostgresCase):
    def test_i7_1_rows_written_under_a_are_in_a_only(self):
        for key, other in (("A", "B"), ("B", "A")):
            with self.subTest(project=key):
                here = self.cluster.rows(self.w[key]["database"], "SELECT pid::text FROM engine.aisc_backend_project")
                there = self.cluster.rows(self.w[other]["database"], "SELECT pid::text FROM engine.aisc_backend_project")
                self.assertEqual(here, [(self.w[key]["pid"],)])
                self.assertNotIn((self.w[key]["pid"],), there)

    def test_i7_8_a_new_evaluation_is_stamped_with_its_own_latest_version(self):
        self.assertEqual(self.w["A"]["system_id"], self.w["versions"][self.w["A"]["platform"]][-1],
                         "I7.8: A's evaluation carries A's latest card version (number 2)")
        self.assertEqual(self.w["B"]["system_id"], self.w["versions"][self.w["B"]["platform"]][-1])

    def test_i7_10_metrics_are_rows_of_each_project(self):
        for key in ("A", "B"):
            with self.subTest(project=key):
                names = self.cluster.rows(self.w[key]["database"], "SELECT name FROM engine.aisc_backend_metric")
                self.assertEqual(names, [("accuracy",)], "one metric per project, made in its own database")

    def test_i7_1_a_query_with_nothing_admitted_fails(self):
        from aisc_backend import projectdb
        from aisc_backend.models import Project

        self.assertIsNone(projectdb.admitted.get(None))
        with self.assertRaises(Exception):
            list(Project.objects.all())


# ── I7.2 with the platform database: membership and the project row are real ──


@configurator_only
class TheDoorOnPostgres(PostgresCase):
    def test_i7_2_a_pid_with_no_core_project_row_is_404(self):
        response = self.api.as_member("GET", "/api/v1/projects", uuid.uuid4(), roles=("admin",))
        self.assertEqual(response.status_code, 404, "I7.2: even an admin, for a project that does not exist")

    def test_i7_2_a_stranger_is_404(self):
        response = self.api.as_member("GET", f"/api/v1/projects/{self.w['A']['pid']}", self.w["A"]["platform"],
                                      subject="mallory")
        self.assertEqual(response.status_code, 404)

    def test_i7_2_a_viewer_reads_and_may_not_write(self):
        path = f"/api/v1/projects/{self.w['A']['pid']}"
        self.assertEqual(self.api.as_member("GET", path, self.w["A"]["platform"], subject="bob").status_code, 200)
        self.assertEqual(self.api.as_member("PATCH", path, self.w["A"]["platform"], subject="bob",
                                            body={"name": "renamed"}).status_code, 403)

    def test_i7_2_a_member_reads_her_project(self):
        response = self.api.as_member("GET", f"/api/v1/projects/{self.w['A']['pid']}", self.w["A"]["platform"])
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["pid"], self.w["A"]["pid"])


# ── I16.5, I7.2, I7.5, I7.11: every route that addresses an object by id ──

#: Bodies are valid for their schema (read from aisc_backend/schemas and the
#: routers), so a refusal cannot come from validation; ("multipart", fields)
#: sends those form fields plus one small file named `file`.
#: (method, path template, body, caller). Filled with A's ids and called with
#: B's project header by alice, who is an owner of both: the answer must be 404,
#: because the object is not in B's database. `worker` calls carry a valid run
#: ticket for (B, A's evaluation), so only the database can refuse them.
ROUTES = [
    ("GET", "/api/v1/projects/{pid}", None, "user"),
    ("PATCH", "/api/v1/projects/{pid}", {"name": "renamed"}, "user"),
    ("GET", "/api/v1/projects/by-name/{name}", None, "user"),
    ("POST", "/api/v1/projects/for-platform/{platform}", None, "user"),
    ("GET", "/api/v1/projects/{pid}/aisystem", None, "user"),
    ("POST", "/api/v1/projects/{pid}/components", {"name": "x", "component_type": "llm"}, "user"),
    ("POST", "/api/v1/projects/{pid}/datasets", {"name": "x"}, "user"),
    ("POST", "/api/v1/projects/{pid}/models", {"name": "x"}, "user"),
    ("GET", "/api/v1/projects/{pid}/evaluations", None, "user"),
    ("GET", "/api/v1/projects/{pid}/evaluation-inputs-template", None, "user"),
    ("GET", "/api/v1/projects/{pid}/plugins/{plugin_name}/config", None, "user"),
    ("POST", "/api/v1/projects/{pid}/measurements/aggregate", {}, "user"),
    ("GET", "/api/v1/projects/{pid}/measurements/dimension-keys", None, "user"),
    ("GET", "/api/v1/projects/{pid}/measurements/dimension-values/{key}", None, "user"),
    ("GET", "/api/v1/components/{component_pid}", None, "user"),
    ("PATCH", "/api/v1/components/{component_pid}", {"name": "renamed"}, "user"),
    ("DELETE", "/api/v1/components/{model_pid}", None, "user"),
    ("PUT", "/api/v1/components/{component_pid}/data", ("multipart", {}), "user"),
    ("GET", "/api/v1/components/{component_pid}/models", None, "user"),
    ("GET", "/api/v1/components/{component_pid}/data", None, "user"),
    ("GET", "/api/v1/evaluations/{evaluation_pid}", None, "user"),
    ("GET", "/api/v1/evaluations/{evaluation_pid}/plugins/status", None, "user"),
    ("GET", "/api/v1/evaluations/{evaluation_pid}/artifacts", None, "user"),
    ("POST", "/api/v1/evaluations/{evaluation_pid}/measurements/aggregate", {}, "user"),
    ("POST", "/api/v1/evaluations/{evaluation_pid}/measurements/dimension-keys", {}, "user"),
    ("POST", "/api/v1/evaluations/{evaluation_pid}/measurements/dimension-values/{key}", {}, "user"),
    ("POST", "/api/v1/evaluations/{evaluation_pid}/measurements/metric-names", {}, "user"),
    ("POST", "/api/v1/evaluations/task", {"project_pid": "{pid}", "plugins_to_run": []}, "user"),
    ("GET", "/api/v1/tasks/{task}/status", None, "user"),
    ("GET", "/api/v1/files/dataset/{dataset_file}", None, "user"),
    ("GET", "/api/v1/files/model/{model_file}", None, "user"),
    ("GET", "/api/v1/files/artifact/{artifact_file}", None, "user"),
    ("GET", "/api/v1/stats/projects/{pid}/overview", None, "user"),
    ("GET", "/api/v1/stats/projects/{pid}/metrics", None, "user"),
    ("GET", "/api/v1/stats/projects/{pid}/plugins", None, "user"),
    ("GET", "/api/v1/stats/projects/{pid}/plugin-durations", None, "user"),
    ("GET", "/api/v1/project/settings/{pid}", None, "user"),
    ("POST", "/api/v1/project/settings/{pid}", {"name": "x", "key": "x", "category": "variables"}, "user"),
    ("GET", "/api/v1/project/settings/{pid}/available", None, "user"),
    ("PATCH", "/api/v1/project/settings/{pid}/{project_config_pid}", {"name": "renamed"}, "user"),
    ("DELETE", "/api/v1/project/settings/{pid}/{project_config_pid}", None, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/feature_flags", None, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/display_icon", None, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/input_definitions", None, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/project_config_definitions", None, "user"),
    ("PATCH", "/api/v1/plugins/{plugin_pid}/enabled", {"enabled": False}, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/configs", None, "user"),
    ("POST", "/api/v1/plugins/{plugin_pid}/configs/{config_id}/restore", None, "user"),
    ("POST", "/api/v1/plugins/{plugin_pid}/config", {"config": {}}, "user"),
    ("POST", "/api/v1/plugins/{plugin_pid}/config/state", {"config": {}}, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/config/state", None, "user"),
    ("GET", "/api/v1/plugins/{plugin_pid}/parse_dataset/{component_pid}/config/state", None, "user"),
    ("GET", "/api/v1/plugins/{evaluation_plugin_pid}/evaluations/{evaluation_pid}/result", None, "user"),
    # I7.5: the body's project_uuid must be the engine project of the header's database
    ("POST", "/api/v1/plugins", {"package_name": "aisc-probe", "version": "1.0.0", "project_uuid": "{pid}"}, "admin"),
    ("POST", "/api/v1/plugins/refresh", {"package_name": "aisc-probe", "version": "1.0.0",
                                         "project_uuid": "{pid}"}, "admin"),
    ("DELETE", "/api/v1/plugins", {"package_name": "aisc-probe", "version": "1.0.0", "project_uuid": "{pid}"},
     "admin"),
    # I7.3: the worker's routes
    ("GET", "/api/v1/internal/evaluations/{evaluation_pid}", None, "worker"),
    ("GET", "/api/v1/internal/evaluations/{evaluation_pid}/inputs", None, "worker"),
    ("GET", "/api/v1/internal/evaluations/{evaluation_pid}/plugins/status", None, "worker"),
    ("PUT", "/api/v1/internal/evaluations/{evaluation_pid}?status=Done", None, "worker"),
    ("PATCH", "/api/v1/internal/evaluations/{evaluation_pid}/plugins/{evaluation_plugin_pid}/timestamp",
     {"field": "started_at"}, "worker"),
    ("PATCH", "/api/v1/internal/evaluations/{evaluation_pid}/plugins/{evaluation_plugin_pid}/fail",
     {"error_message": "x"}, "worker"),
    ("POST", "/api/v1/internal/evaluations/{evaluation_pid}/measures", {"{evaluation_plugin_pid}": []}, "worker"),
    ("POST", "/api/v1/internal/evaluations/{evaluation_pid}/artifacts",
     ("multipart", {"evaluation_plugin_uuid": "{evaluation_plugin_pid}"}), "worker"),
    ("GET", "/api/v1/internal/projects/settings/{pid}", None, "worker"),
    ("POST", "/api/v1/internal/projects/settings/{pid}/by-pid", {"project_config_selections": []}, "worker"),
    ("GET", "/api/v1/internal/files/dataset/{dataset_file}", None, "worker"),
    ("GET", "/api/v1/internal/files/model/{model_file}", None, "worker"),
]

#: Routes with a path parameter that address no object of a project, with the reason.
NOT_BY_ID = {}

#: Read routes that, called under A's own header, must find A's object: proves the
#: 404 under B is the database's doing, not a broken route.
CONTROLS = [
    "/api/v1/projects/{pid}", "/api/v1/projects/by-name/{name}", "/api/v1/projects/{pid}/aisystem",
    "/api/v1/projects/{pid}/evaluations", "/api/v1/components/{component_pid}",
    "/api/v1/evaluations/{evaluation_pid}?include=plugin", "/api/v1/evaluations/{evaluation_pid}/plugins/status",
    "/api/v1/project/settings/{pid}", "/api/v1/stats/projects/{pid}/overview",
]


def _fill(value, ids):
    if isinstance(value, tuple):
        return (value[0], _fill(value[1], ids))
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


@configurator_only
class EveryRouteByIdIsInTheTable(unittest.TestCase):
    """No database needed: every route of the API with a path parameter, and
    every route that takes a project in its body, is in ROUTES (or NOT_BY_ID
    with a reason), so a route added later cannot skip the isolation test."""

    BODY_ADDRESSED = {("POST", "/api/v1/evaluations/task"), ("POST", "/api/v1/plugins"),
                      ("POST", "/api/v1/plugins/refresh"), ("DELETE", "/api/v1/plugins")}

    def test_i16_5_the_table_covers_the_api(self):
        import re

        from aisc_backend.tests.test_every_project_route_is_guarded import operations_of
        from config.urls import api

        def shape(p):
            return re.sub(r"\{[a-z_]+\}", "{}", p.split("?")[0])

        covered = {(m, shape(p)) for m, p, _, _ in ROUTES} | set(NOT_BY_ID)
        missing = sorted({(m, shape(p)) for m, p, _ in operations_of(api)
                          if ("{" in p or (m, p) in self.BODY_ADDRESSED)} - covered)
        self.assertEqual(missing, [], "I16.5: routes by id without a cross-project test")


@configurator_only
class NothingOfAIsReachableUnderB(PostgresCase):
    def _ids(self):
        return dict(self.w["A"], key="k", plugin_name="probe")

    def _call(self, method, path, body, caller, header):
        if caller == "worker":
            return self.api.as_worker(method, path, header, self.w["A"]["evaluation_pid"], body)
        roles = ("admin",) if caller == "admin" else ("primary-user",)
        return self.api.as_member(method, path, header, body=body, roles=roles)

    def test_i16_5_i7_2_every_route_by_id_is_404_under_another_project(self):
        ids = self._ids()
        for method, template, body, caller in ROUTES:
            with self.subTest(route=f"{method} {template}"):
                response = self._call(method, _fill(template, ids), _fill(body, ids), caller, self.w["B"]["platform"])
                self.assertEqual(response.status_code, 404,
                                 f"I16.5: A's object answered {response.status_code} under B's project")

    def test_i16_5_the_same_reads_under_a_find_the_object(self):
        ids = self._ids()
        for template in CONTROLS:
            with self.subTest(route=template):
                response = self.api.as_member("GET", _fill(template, ids), self.w["A"]["platform"])
                self.assertEqual(response.status_code, 200, response.content[:300])

    def test_i7_3_the_worker_reads_its_own_evaluation(self):
        response = self.api.as_worker(
            "GET", f"/api/v1/internal/evaluations/{self.w['A']['evaluation_pid']}?include=project,plugin",
            self.w["A"]["platform"], self.w["A"]["evaluation_pid"])
        self.assertEqual(response.status_code, 200, response.content[:300])

    def test_i7_2_listings_under_b_hold_nothing_of_a(self):
        projects = self.api.as_member("GET", "/api/v1/projects", self.w["B"]["platform"])
        self.assertEqual(projects.status_code, 200)
        self.assertEqual([p["pid"] for p in projects.json()], [self.w["B"]["pid"]])
        evaluations = self.api.as_member("GET", "/api/v1/evaluations?status=Done", self.w["B"]["platform"])
        self.assertEqual(evaluations.status_code, 200)
        self.assertNotIn(self.w["A"]["evaluation_pid"], [e["evaluation_pid"] for e in evaluations.json()])

    def test_i7_5_a_plugin_for_the_header_project_names_its_own_engine_project(self):
        body = {"package_name": "aisc-probe", "version": "1.0.0", "project_uuid": self.w["B"]["pid"]}
        response = self.api.as_member("POST", "/api/v1/plugins", self.w["A"]["platform"], body=body, roles=("admin",))
        self.assertEqual(response.status_code, 404, "I7.5: B's engine project named under A's header")


# ── I2.5, I17.1: a dropped database, and no connection kept ──


@configurator_only
class ADroppedDatabase(PostgresCase):
    def test_i2_5_after_the_drop_the_project_is_404_and_the_alias_is_gone(self):
        from django.db import connections

        c = self.w["C"]
        first = self.api.as_member("GET", "/api/v1/projects", c)
        self.assertEqual(first.status_code, 200, "C's database was migrated and opens")
        self.cluster.drop_database(database_name(c))
        second = self.api.as_member("GET", "/api/v1/projects", c)
        self.assertEqual(second.status_code, 404, "I2.5: a dropped project database is 404, not 500")
        self.assertNotIn(database_name(c), connections.settings, "I2.5: the alias is evicted")

    def test_i17_1_no_connection_is_kept_after_a_request(self):
        self.api.as_member("GET", f"/api/v1/projects/{self.w['A']['pid']}", self.w["A"]["platform"])
        for _ in range(20):
            left = self.cluster.rows(None, "SELECT count(*) FROM pg_stat_activity "
                                           "WHERE usename = 'engine_rw' AND datname = %s", (self.w["A"]["database"],))[0][0]
            if left == 0:
                break
            time.sleep(0.25)
        self.assertEqual(left, 0, "I17.1: CONN_MAX_AGE=0, the project connection is closed after the request")
