"""WP1 of pipeline-2026-09-23: the engine is back to one AISystem per project.

The engine data model is frozen at e34fca3 (Sean's merge). Migration 0022 moved
the parts of the system onto a platform version; 0023 undoes that, and the code
returns to e34fca3. These tests pin the end state: the models, the migration
graph, and the two routes whose behaviour 0022's code changed.

Rules covered (03-specs.md): S1.3, S1.6, and the WP1 interface lines
(GET /projects/{pid}/aisystem shape; POST /evaluations/task without the
platform). S1.1, S1.4 and S1.5 are checked by scripts/guard-frozen.sh (G1, G4,
G5) and by the full suite.
"""
import unittest.mock as mock
import uuid

import httpx
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend import models as engine_models
from aisc_backend.auth import keycloak
from aisc_backend.models import AIComponent, AIComponentType, Project, ProjectStatus
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.project import router as project_router

SIGNED_IN = {"Authorization": "Bearer development"}
MIGRATION_0022 = "0022_parts_belong_to_a_version_of_the_one_system"
MIGRATION_0023 = "0023_one_system_per_project_again"

projects = TestAsyncClient(project_router)
evaluations = TestAsyncClient(evaluation_router)

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


def _aisystem_model(test):
    model = getattr(engine_models, "AISystem", None)
    if model is None:
        test.fail("aisc_backend.models has no AISystem: the e34fca3 model is not restored (WP1)")
    return model


class ModelStateIsE34fca3(TestCase):
    """The models equal e34fca3: AIComponent.system, no version fields."""

    def test_s1_aisystem_model_is_back_on_table_ai_system(self):
        AISystem = _aisystem_model(self)
        self.assertEqual(AISystem._meta.db_table, "ai_system")
        project_field = AISystem._meta.get_field("project")
        self.assertTrue(project_field.one_to_one, "AISystem.project must be a OneToOneField")

    def test_s1_component_belongs_to_the_system(self):
        field_names = {f.name for f in AIComponent._meta.get_fields()}
        self.assertIn("system", field_names, "AIComponent.system (FK to AISystem) is missing")
        self.assertEqual(AIComponent._meta.get_field("system").related_model.__name__, "AISystem")

    def test_s1_component_has_no_version_fields(self):
        field_names = {f.name for f in AIComponent._meta.get_fields()}
        for gone in ("project", "system_version_id", "lineage"):
            self.assertNotIn(gone, field_names, f"AIComponent.{gone} must be removed (WP1)")

    def test_s1_no_system_version_parts_model(self):
        self.assertFalse(hasattr(engine_models, "SystemVersionParts"),
                         "SystemVersionParts must be deleted (WP1)")


class Migration0023Exists(TestCase):
    """0023 follows 0022, and 0022 stays."""

    def test_s1_0023_depends_on_0022(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        self.assertIn(("aisc_backend", MIGRATION_0022), loader.disk_migrations)
        key = ("aisc_backend", MIGRATION_0023)
        self.assertIn(key, loader.disk_migrations, f"migration {MIGRATION_0023} is missing")
        self.assertIn(("aisc_backend", MIGRATION_0022), loader.disk_migrations[key].dependencies)

    def test_s1_0023_is_the_leaf(self):
        # Nothing after 0023 changes the data model. The one migration allowed
        # on top of it drops the tables of the login the engine no longer has
        # (0024), none of which is an engine model.
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        self.assertEqual(loader.graph.leaf_nodes("aisc_backend"),
                         [("aisc_backend", "0024_no_login_of_its_own")])
        parents = loader.graph.node_map[("aisc_backend", "0024_no_login_of_its_own")].parents
        self.assertEqual({p.key for p in parents if p.key[0] == "aisc_backend"},
                         {("aisc_backend", MIGRATION_0023)})

    def test_s1_3_0023_applied_on_sqlite_without_core(self):
        # S1.3: on sqlite 0023 succeeds and skips every core-related statement.
        if connection.vendor != "sqlite":
            self.skipTest("S1.3 is about sqlite")
        loader = MigrationLoader(connection)
        self.assertIn(("aisc_backend", MIGRATION_0023), loader.applied_migrations,
                      "0023 did not run on sqlite")
        with connection.cursor() as cursor:
            cursor.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
            ddl = "\n".join(row[0] for row in cursor.fetchall()).lower()
        self.assertNotIn("core.", ddl)
        self.assertNotIn("system_version_parts", ddl)


class OneSystemPerProject(TestCase):
    """S1.6: an engine project has at most one ai_system (UNIQUE project_id)."""

    def test_s1_6_second_system_for_a_project_is_refused(self):
        AISystem = _aisystem_model(self)
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        AISystem.objects.create(project=project, name="p system", description="")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AISystem.objects.create(project=project, name="again", description="")


COMPONENT_KEYS = {"pid", "name", "description", "component_type", "data", "file_size",
                  "json_value", "source_dataset_pid"}


class AISystemRouteIsE34fca3(TestCase):
    """GET /api/v1/projects/{pid}/aisystem returns {pid, name, description, components[]}."""

    async def _project(self, **kw):
        return await Project.objects.acreate(name="MCAS", status=ProjectStatus.Ready, **kw)

    async def test_s1_aisystem_shape_and_one_stable_system(self):
        project = await self._project()
        made = await projects.post(f"/{project.pid}/components",
                                   json={"name": "Loans", "component_type": "dataset"},
                                   headers=SIGNED_IN)
        self.assertEqual(made.status_code, 200, made.content)

        first = await projects.get(f"/{project.pid}/aisystem", headers=SIGNED_IN)
        self.assertEqual(first.status_code, 200, first.content)
        body = first.json()
        self.assertEqual(set(body), {"pid", "name", "description", "components"})
        self.assertIsNotNone(body["pid"], "the system has its own pid (the AISystem row)")
        self.assertEqual(body["name"], "MCAS system")
        self.assertEqual(body["description"], "")
        self.assertEqual(len(body["components"]), 1)
        self.assertEqual(set(body["components"][0]), COMPONENT_KEYS)

        again = await projects.get(f"/{project.pid}/aisystem", headers=SIGNED_IN)
        self.assertEqual(again.json()["pid"], body["pid"], "one system per project, reused")

    async def test_s1_aisystem_does_not_ask_the_platform(self):
        # A project the platform knows, with the platform unreachable: the
        # e34fca3 route never calls it, so it answers.
        project = await self._project(platform_project_id=uuid.uuid4())
        with mock.patch.dict("os.environ", {"PLATFORM_URL": "http://127.0.0.1:9"}):
            response = await projects.get(f"/{project.pid}/aisystem", headers=SIGNED_IN)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"pid", "name", "description", "components"})


class EvaluationTaskWithoutThePlatform(TestCase):
    """POST /api/v1/evaluations/task no longer depends on the platform."""

    async def _project_with_part(self):
        project = await Project.objects.acreate(name="MCAS", status=ProjectStatus.Ready)
        made = await projects.post(f"/{project.pid}/components",
                                   json={"name": "Loans", "component_type": "dataset"},
                                   headers=SIGNED_IN)
        self.assertEqual(made.status_code, 200, made.content)
        # Linked to the platform only now, so the part was made without it.
        project.platform_project_id = uuid.uuid4()
        await project.asave(update_fields=["platform_project_id"])
        return project, made.json()["pid"]

    async def _run(self, project, inputs):
        from aisc_backend.models import Plugin, PluginConfig

        plugin = await Plugin.objects.acreate(
            name="Demo", display_name="Demo", description="", package_name="demo",
            version="0.1", project=project, enabled=True,
        )
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        plugin.current_config = config
        await plugin.asave()
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch("aisc_backend.routers.evaluation.plugin_loader.load_plugin",
                        return_value=mock.Mock(project_config_definitions=[])), \
             mock.patch("aisc_backend.routers.evaluation.validate_plugin_settings",
                        new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch("aisc_backend.routers.evaluation.celery_service.run_evaluation",
                        new=mock.AsyncMock(return_value=mock.Mock(task_id=str(uuid.uuid4())))):
            return await evaluations.post(
                "/task",
                json={"project_pid": str(project.pid),
                      "plugins_to_run": [{"name": "Demo", "inputs": [
                          {"name": "data", "pid": pid} for pid in inputs]}]},
                headers=SIGNED_IN,
            )

    async def test_s1_no_503_when_the_platform_is_down(self):
        from aisc_backend.models import Evaluation

        project, part = await self._project_with_part()
        with mock.patch.dict("os.environ", {"PLATFORM_URL": "http://127.0.0.1:9"}):
            response = await self._run(project, [part])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(await Evaluation.objects.acount(), 1)

    async def test_s1_no_rejection_of_inputs_outside_the_version(self):
        # Whatever a platform would say about versions, the engine does not ask:
        # an input of the project is accepted.
        project, part = await self._project_with_part()
        version = {"pid": str(uuid.uuid4()), "number": 2, "name": "MCAS",
                   "frozen_at": None, "frozen_reason": None}
        answer = httpx.Response(200, json={"current": version, "versions": [version]},
                                request=httpx.Request("GET", "http://platform/"))
        with mock.patch.dict("os.environ", {"PLATFORM_URL": "http://platform"}), \
             mock.patch("httpx.request", return_value=answer):
            response = await self._run(project, [part])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(b"not parts of the version", response.content)
