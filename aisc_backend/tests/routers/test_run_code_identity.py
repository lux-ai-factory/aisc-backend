"""What code a run ran on is recorded: the worker gets each plugin's recorded wheel hash and dependency lock
with the run, so it installs only those; and when a local plugin starts, the worker reports its code digest,
commit and dirty flag, which the run keeps. A malformed code identity is refused."""
import os
from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.internal import router as internal_router

client = TestAsyncClient(internal_router)
SECRET = "fake-internal-secret"
HEADERS = {"X-Internal-Secret": SECRET}
HASH = "a" * 64
LOCK = f"demo-pkg==0.1.0 \\\n    --hash=sha256:{HASH}\n"
CODE = {"digest": "d" * 64, "git_commit": "c" * 40, "git_dirty": True}


class RunCodeIdentityTestCase(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Processing, project=project)
        fields = {f.name for f in Plugin._meta.get_fields()}
        recorded = {k: v for k, v in (("wheel_sha256", HASH), ("dependency_lock", LOCK)) if k in fields}
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                       version="0.1.0", project=project, **recorded)
        config = PluginConfig.objects.create(plugin=plugin, config={})
        self.run = EvaluationPlugin.objects.create(evaluation=self.evaluation, plugin_config=config)
        self.path = f"/evaluations/{self.evaluation.pid}/plugins/{self.run.pid}/timestamp"

    async def test_the_started_mark_stores_the_local_plugins_code_identity(self):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": SECRET}):
            response = await client.patch(self.path, json={"field": "started_at", "code": CODE}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.content[:300])
        row = await EvaluationPlugin.objects.aget(pk=self.run.pk)
        self.assertEqual((getattr(row, "code_digest", None), getattr(row, "code_commit", None),
                          getattr(row, "code_dirty", None)), (CODE["digest"], CODE["git_commit"], True))

    async def test_a_malformed_code_identity_is_refused(self):
        for code in ({**CODE, "digest": "xyz"}, {**CODE, "git_commit": "not-a-commit"}, {**CODE, "extra": 1}):
            with self.subTest(code=sorted(code)), patch.dict(os.environ, {"INTERNAL_API_KEY": SECRET}):
                response = await client.patch(self.path, json={"field": "started_at", "code": code},
                                              headers=HEADERS)
                self.assertEqual(response.status_code, 422, response.content[:300])

    async def test_the_worker_gets_each_plugins_pins_with_the_run(self):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": SECRET}):
            response = await client.get(f"/evaluations/{self.evaluation.pid}", headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.content[:300])
        [entry] = response.json()["evaluation_plugins"]
        self.assertEqual(entry.get("pins"), {"wheel_sha256": HASH, "dependency_lock": LOCK}, sorted(entry))


class LaunchCopiesTheWheelHash(TestCase):
    """A launched run keeps the wheel hash its plugin recorded at that moment: a later install of other code
    under the same row does not change what the run says it ran."""

    async def test_the_run_keeps_its_plugins_wheel_hash(self):
        from unittest import mock

        from aisc_backend.auth import keycloak
        from aisc_backend.routers.evaluation import router as evaluation_router
        from aisc_backend.tests.stored_manifest import as_declared

        project = await Project.objects.acreate(name="p", status=ProjectStatus.Ready)
        plugin = await Plugin.objects.acreate(name="Demo", display_name="Demo", package_name="demo",
                                              version="0.1", project=project, enabled=True,
                                              wheel_sha256=HASH, dependency_lock=LOCK)
        plugin.current_config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        await plugin.asave()
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        with mock.patch.object(keycloak, "AUTH_ENABLED", False), \
             as_declared(mock.Mock(project_config_definitions=[], target_kinds=())), \
             mock.patch("aisc_backend.routers.evaluation.validate_plugin_settings",
                        new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch("aisc_backend.routers.evaluation.celery_service.run_evaluation",
                        new=mock.AsyncMock(return_value=mock.Mock(task_id="0c5e7a10-0000-4000-8000-000000000001"))):
            response = await TestAsyncClient(evaluation_router).post(
                "/task", json={"project_pid": str(project.pid), "plugins_to_run": [{"name": "Demo", "inputs": []}]},
                headers={"Authorization": "Bearer development"})
        self.assertEqual(response.status_code, 200, response.content[:300])
        run = await EvaluationPlugin.objects.filter(plugin_config__plugin=plugin).aget()
        self.assertEqual(run.wheel_sha256, HASH)
