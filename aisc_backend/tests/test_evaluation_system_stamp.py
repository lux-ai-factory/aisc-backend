"""WP9 of pipeline-2026-09-23: a test records the system version it ran under.

engine.evaluation.system_id (frozen column, FK to core.system(pid)) gets the pid of
the latest core.system row of the project (highest `number`) when the evaluation
is created with system_id NULL. Per amendment A1 this is done by new, non-Sean
code (a pre_save signal on Evaluation plus
aisc_backend/repositories/system_version_repository.latest_system_pid), never by
an edit of routers/evaluation.py or models/evaluation.py (see
test_frozen_sean_files.py).

The Postgres tests make a minimal core.system (pid, project_id, number: the WP2
shape) inside the test transaction; they skip on sqlite. Run them against a
throwaway Postgres, never the live DB.

Rules covered: S9.2, S9.3 (and the WP9 interface of latest_system_pid).
"""
import asyncio
import unittest.mock as mock
import uuid

from django.db import connection
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models import Project, ProjectStatus
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.routers.evaluation import router as evaluation_router

SIGNED_IN = {"Authorization": "Bearer development"}
evaluations = TestAsyncClient(evaluation_router)

_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


def _latest_system_pid(test):
    try:
        from aisc_backend.repositories.system_version_repository import latest_system_pid
    except ImportError as exc:
        test.fail(f"aisc_backend.repositories.system_version_repository.latest_system_pid "
                  f"is missing (WP9): {exc}")
    return latest_system_pid


def _postgres_only(test):
    if connection.vendor != "postgresql":
        test.skipTest("needs Postgres with core.system (run against a throwaway Postgres)")


def _make_core_system():
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS core")
        cursor.execute("CREATE TABLE IF NOT EXISTS core.system ("
                       " pid uuid PRIMARY KEY, project_id uuid NOT NULL,"
                       " number integer NOT NULL CHECK (number > 0),"
                       " UNIQUE (project_id, number))")


def _add_version(platform_project, number) -> uuid.UUID:
    pid = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO core.system (pid, project_id, number) VALUES (%s, %s, %s)",
                       [str(pid), str(platform_project), number])
    return pid


async def _start(project):
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
                  "plugins_to_run": [{"name": "Demo", "inputs": []}]},
            headers=SIGNED_IN,
        )


class LatestSystemPid(TestCase):
    """The repository function of the WP9 interface."""

    def test_s9_latest_system_pid_is_an_async_function(self):
        latest_system_pid = _latest_system_pid(self)
        self.assertTrue(asyncio.iscoroutinefunction(latest_system_pid))

    async def test_s9_3_none_for_no_platform_project(self):
        latest_system_pid = _latest_system_pid(self)
        self.assertIsNone(await latest_system_pid(None))

    async def test_s9_3_none_on_sqlite(self):
        if connection.vendor != "sqlite":
            self.skipTest("about sqlite")
        latest_system_pid = _latest_system_pid(self)
        self.assertIsNone(await latest_system_pid(uuid.uuid4()))

    async def test_s9_3_none_when_core_system_is_absent(self):
        _postgres_only(self)
        latest_system_pid = _latest_system_pid(self)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('core.system') IS NULL")
            if not cursor.fetchone()[0]:
                self.skipTest("this test DB already has core.system")
        self.assertIsNone(await latest_system_pid(uuid.uuid4()))

    def test_s9_2_highest_number_wins(self):
        _postgres_only(self)
        latest_system_pid = _latest_system_pid(self)
        _make_core_system()
        platform_project = uuid.uuid4()
        _add_version(platform_project, 1)
        v2 = _add_version(platform_project, 2)
        _add_version(uuid.uuid4(), 7)  # another project's version is never picked
        from asgiref.sync import async_to_sync
        self.assertEqual(async_to_sync(latest_system_pid)(platform_project), v2)

    def test_s9_3_none_when_the_project_has_no_version(self):
        _postgres_only(self)
        latest_system_pid = _latest_system_pid(self)
        _make_core_system()
        from asgiref.sync import async_to_sync
        self.assertIsNone(async_to_sync(latest_system_pid)(uuid.uuid4()))


class EvaluationCarriesTheLatestVersion(TestCase):
    """S9.2 and S9.3 through the model and the start route."""

    def test_s9_2_new_evaluation_gets_the_latest_version_and_keeps_it(self):
        _postgres_only(self)
        _make_core_system()
        platform_project = uuid.uuid4()
        project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                         platform_project_id=platform_project)
        _add_version(platform_project, 1)
        v2 = _add_version(platform_project, 2)

        evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.system_id, v2)

        _add_version(platform_project, 3)
        evaluation.status = EvaluationStatus.Pending
        evaluation.save()
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.system_id, v2, "a later version never restamps a test")

    def test_s9_2_an_explicit_system_id_is_kept(self):
        _postgres_only(self)
        _make_core_system()
        platform_project = uuid.uuid4()
        project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                         platform_project_id=platform_project)
        v1 = _add_version(platform_project, 1)
        _add_version(platform_project, 2)
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project,
                                               system_id=v1)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.system_id, v1)

    async def test_s9_2_started_through_the_route(self):
        _postgres_only(self)
        from asgiref.sync import sync_to_async
        await sync_to_async(_make_core_system)()
        platform_project = uuid.uuid4()
        project = await Project.objects.acreate(name="MCAS", status=ProjectStatus.Ready,
                                                platform_project_id=platform_project)
        await sync_to_async(_add_version)(platform_project, 1)
        v2 = await sync_to_async(_add_version)(platform_project, 2)

        response = await _start(project)
        self.assertEqual(response.status_code, 200, response.content)
        evaluation = await Evaluation.objects.aget(pid=response.json()["pid"])
        self.assertEqual(evaluation.system_id, v2)

        await sync_to_async(_add_version)(platform_project, 3)
        evaluation = await Evaluation.objects.aget(pid=response.json()["pid"])
        self.assertEqual(evaluation.system_id, v2)

    def test_s9_3_no_version_gives_null(self):
        _postgres_only(self)
        _make_core_system()
        project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                         platform_project_id=uuid.uuid4())
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        evaluation.refresh_from_db()
        self.assertIsNone(evaluation.system_id)

    async def test_s9_3_route_without_a_version_starts_with_null(self):
        # Any engine: a project the platform knows but with no version yet.
        project = await Project.objects.acreate(name="MCAS", status=ProjectStatus.Ready,
                                                platform_project_id=uuid.uuid4())
        response = await _start(project)
        self.assertEqual(response.status_code, 200, response.content)
        evaluation = await Evaluation.objects.aget(pid=response.json()["pid"])
        self.assertIsNone(evaluation.system_id)

    def test_s9_3_sqlite_gives_null(self):
        if connection.vendor != "sqlite":
            self.skipTest("about sqlite")
        project = Project.objects.create(name="MCAS", status=ProjectStatus.Ready,
                                         platform_project_id=uuid.uuid4())
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=project)
        evaluation.refresh_from_db()
        self.assertIsNone(evaluation.system_id)
