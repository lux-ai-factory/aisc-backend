"""What each engine route records in the project's ledger (aisc_backend.ledger).

The routes are called as the door would let them run: a project admitted, the gateway's request id
held, LEDGER_MODE on. Each test reads the events the route left in the outbox.
"""
import json
import os
from unittest import mock

from asgiref.sync import sync_to_async
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend import ledger, projectdb
from aisc_backend.models import (AIComponent, AIComponentType, LedgerOutbox, Project, ProjectConfig,
                                 ProjectConfigCategory, ProjectStatus)
from aisc_backend.models.ai_system import AISystem
from aisc_backend.repositories.ai_component_repository import AIComponentRepository
from aisc_backend.repositories.project_config_repository import ProjectConfigRepository
from aisc_backend.routers.component import router as component_router
from aisc_backend.routers.platform_project import router as platform_project_router
from aisc_backend.routers.project import router as project_router
from aisc_backend.routers.project_config import router as project_config_router

PID = "0f0e0d0c-0b0a-4908-8706-050403020100"
REQUEST = "11111111-2222-4333-8444-555555555555"

components = TestAsyncClient(component_router)
projects = TestAsyncClient(project_router)
settings_client = TestAsyncClient(project_config_router)
for_platform = TestAsyncClient(platform_project_router)


async def events() -> list[dict]:
    return [row.event async for row in LedgerOutbox.objects.order_by("id")]


def unrecorded():
    """The ledger off, for the rows a test starts from."""
    return mock.patch.dict(os.environ, {"LEDGER_MODE": "off"})


class Recording(TestCase):
    """LEDGER_MODE on, PID admitted, REQUEST witnessed."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"LEDGER_MODE": "record"})
        patcher.start()
        self.addCleanup(patcher.stop)
        pid, request = projectdb.admitted_pid.set(PID), ledger.current_request.set(REQUEST)
        self.addCleanup(projectdb.admitted_pid.reset, pid)
        self.addCleanup(ledger.current_request.reset, request)
        with unrecorded():
            self.project = Project.objects.create(name="p", status=ProjectStatus.Ready, platform_project_id=PID)
            self.system = AISystem.objects.create(project=self.project, name="sys", description="")

    def assertEvent(self, event, action, item_type, item_id, details):
        self.assertEqual((event["action"], event["item_type"], event["item_id"], event["details"]),
                         (action, item_type, str(item_id), details))
        self.assertEqual(event["request_id"], REQUEST)


class Components(Recording):
    async def _component(self, component_type=AIComponentType.DATASET, **kw):
        with unrecorded():
            return await AIComponent.objects.acreate(component_type=component_type, system=self.system, name="c",
                                                     description="", data="", **kw)

    async def test_creating_a_component(self):
        res = await projects.post(f"/{self.project.pid}/components",
                                  json={"name": "data", "component_type": "dataset"})
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.component.created", "component", res.json()["pid"],
                         {"component_type": "dataset"})

    async def test_updating_a_component_names_the_fields_that_changed(self):
        comp = await self._component(AIComponentType.LLM)
        res = await components.patch(f"/{comp.pid}", json={"name": "renamed", "description": ""})
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.component.updated", "component", comp.pid, {"changed": ["name"]})

    async def test_uploading_a_components_file(self):
        comp = await self._component()
        upload = SimpleUploadedFile("rows.csv", b"a,b\n1,2\n", content_type="text/csv")
        with mock.patch("aisc_backend.routers.component.file_repository.upload_file", return_value=True):
            res = await components.put(f"/{comp.pid}/data", FILES={"file": upload})
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.component.file_uploaded", "component", comp.pid, {"size": 8})

    async def test_deleting_a_component(self):
        comp = await self._component(AIComponentType.MODEL)
        res = await components.delete(f"/{comp.pid}")
        self.assertEqual(res.status_code, 204, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.component.deleted", "component", comp.pid, {"component_type": "model"})

    async def test_the_platforms_target_mirror_is_not_a_persons_change(self):
        from aisc_backend.routers import target_input

        await sync_to_async(AIComponent.objects.create)(
            system=self.system, name="Target", component_type="resource",
            json_value={"value": target_input.system_reference(PID)})
        self.assertEqual(await events(), [])

    async def test_a_change_whose_event_cannot_be_written_is_not_made(self):
        comp = await self._component()
        comp.name = "renamed"
        with mock.patch.object(ledger, "emit", side_effect=RuntimeError("the outbox is full")):
            with self.assertRaises(RuntimeError):
                await AIComponentRepository().save(comp)
        self.assertEqual((await AIComponent.objects.aget(pk=comp.pk)).name, "c")

    async def test_nothing_is_recorded_while_the_ledger_is_off(self):
        with mock.patch.dict(os.environ, {"LEDGER_MODE": "off"}):
            res = await projects.post(f"/{self.project.pid}/components",
                                      json={"name": "data", "component_type": "dataset"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(await events(), [])


class Projects(Recording):
    async def test_renaming_the_project(self):
        res = await projects.patch(f"/{self.project.pid}", json={"name": "renamed"})
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.project.updated", "engine_project", self.project.pid,
                         {"changed": ["name"]})

    async def test_the_first_visit_of_a_platform_project_makes_its_row(self):
        other = "0f0e0d0c-0b0a-4908-8706-0504030201ff"
        projectdb.admitted_pid.set(other)
        res = await for_platform.post(f"/{other}")
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.project.created", "engine_project", res.json()["pid"], {})
        res = await for_platform.post(f"/{other}")
        self.assertEqual(len(await events()), 1)


class Settings(Recording):
    SECRET = "sk-live-0123456789abcdefghij"

    async def test_creating_a_secret_names_its_key_and_never_its_value(self):
        res = await settings_client.post(f"/{self.project.pid}", json={
            "category": "secrets", "key": "OPENAI_KEY", "name": "OpenAI", "value": self.SECRET})
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.secret.created", "secret", res.json()["pid"],
                         {"key": res.json()["key"]})
        self.assertNotIn(self.SECRET, json.dumps(event))
        self.assertNotIn(self.SECRET[-4:], json.dumps(event))

    async def test_creating_a_setting(self):
        res = await settings_client.post(f"/{self.project.pid}", json={
            "category": "variables", "key": "THRESHOLD", "name": "Threshold",
            "json_value": {"type": "number", "value": 0.7}})
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.setting.created", "setting", res.json()["pid"],
                         {"key": res.json()["key"]})
        self.assertNotIn("0.7", json.dumps(event))

    async def test_changing_a_secret_says_its_value_changed_and_not_to_what(self):
        # The upstream PATCH route fails on SQLite before it writes (the async lazy load of
        # project_config.project), so its write is made here as the route makes it.
        with unrecorded():
            secret = await ProjectConfig.objects.acreate(project=self.project, key="K", name="k",
                                                         category=ProjectConfigCategory.SECRETS, encrypted_value="old")
        secret.encrypted_value, secret.masked_value = "new-ciphertext", "sk-l...ghij"
        await ProjectConfigRepository().save(secret)
        [event] = await events()
        self.assertEvent(event, "engine.secret.updated", "secret", secret.pid, {"key": "K", "changed": ["value"]})
        self.assertNotIn("ciphertext", json.dumps(event))

    async def test_deleting_a_setting(self):
        with unrecorded():
            setting = await ProjectConfig.objects.acreate(project=self.project, key="K", name="k",
                                                          category=ProjectConfigCategory.VARIABLES, json_value={"value": 1})
        res = await settings_client.delete(f"/{self.project.pid}/{setting.pid}")
        self.assertEqual(res.status_code, 204, res.text)
        [event] = await events()
        self.assertEvent(event, "engine.setting.deleted", "setting", setting.pid, {"key": "K"})


INTERNAL_SECRET = "test-internal-secret"


class Runs(Recording):
    """An evaluation's run: started by a person's request, then the worker's internal calls."""

    def setUp(self):
        super().setUp()
        from aisc_backend.models import Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig

        with unrecorded():
            plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                           version="0.1.0", project=self.project)
            config = PluginConfig.objects.create(plugin=plugin, config={})
            self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project)
            self.evaluation_plugin = EvaluationPlugin.objects.create(evaluation=self.evaluation,
                                                                     plugin_config=config)
        self.internal = TestAsyncClient(__import__("aisc_backend.routers.internal", fromlist=["router"]).router)
        patcher = mock.patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _start(self):
        from aisc_backend.services import celery_service

        send = mock.Mock(return_value=mock.Mock(task_id="00000000-0000-4000-8000-000000000009"))
        with mock.patch.object(celery_service.celery, "send_task", new=send):
            await celery_service.run_evaluation(self.evaluation.pid)
        return send

    async def _as_the_worker(self, method, path, **kw):
        # the worker's calls carry no witnessed request of their own
        token = ledger.current_request.set(None)
        try:
            return await getattr(self.internal, method)(path, headers={"X-Internal-Secret": INTERNAL_SECRET}, **kw)
        finally:
            ledger.current_request.reset(token)

    def assertRunEvent(self, event, action, item_type, item_id, details):
        self.assertEvent(event, action, item_type, item_id, details)
        self.assertEqual(event["run_id"], str(self.evaluation.pid))

    async def test_starting_a_run_records_it_before_the_worker_is_sent(self):
        send = await self._start()
        send.assert_called_once()
        [event] = await events()
        self.assertRunEvent(event, "engine.evaluation.run_requested", "evaluation", self.evaluation.pid,
                            {"plugins": ["DemoPlugin"]})

    async def test_a_run_local_from_its_creation_says_so_when_it_starts(self):
        from aisc_backend.models import EvaluationInput

        with unrecorded():
            model = await AIComponent.objects.acreate(component_type=AIComponentType.MODEL, system=self.system,
                                                      name="model.onnx", description="", data="")
            await EvaluationInput.objects.acreate(evaluation_plugin=self.evaluation_plugin, name="model",
                                                  component=model, value={"local_model": {"file": "model.onnx"}})
            await EvaluationInput.objects.acreate(evaluation_plugin=self.evaluation_plugin, name="dataset",
                                                  component=model, value={"threshold": 3})
        await self._start()
        [event] = await events()
        self.assertRunEvent(event, "engine.evaluation.run_requested", "evaluation", self.evaluation.pid,
                            {"plugins": ["DemoPlugin"],
                             "local_models": [{"plugin": "DemoPlugin", "input": "model", "file": "model.onnx"}]})

    async def test_a_run_that_could_not_be_sent_is_not_recorded(self):
        from aisc_backend.services import celery_service

        with mock.patch.object(celery_service.celery, "send_task", side_effect=ConnectionError("broker down")):
            with self.assertRaises(ConnectionError):
                await celery_service.run_evaluation(self.evaluation.pid)
        self.assertEqual(await events(), [])

    async def test_the_workers_status_changes_cite_the_runs_request(self):
        await self._start()
        res = await self._as_the_worker("patch", f"/evaluations/{self.evaluation.pid}/plugins/"
                                                 f"{self.evaluation_plugin.pid}/timestamp", json={"field": "started_at"})
        self.assertEqual(res.status_code, 200, res.text)
        res = await self._as_the_worker("put", f"/evaluations/{self.evaluation.pid}?status=Processing")
        self.assertEqual(res.status_code, 200, res.text)
        _, plugin_running, running = await events()
        self.assertRunEvent(plugin_running, "engine.evaluation.plugin_status_changed", "evaluation",
                            self.evaluation.pid,
                            {"plugin": "DemoPlugin", "status_before": "Pending", "status_after": "Running"})
        self.assertRunEvent(running, "engine.evaluation.status_changed", "evaluation", self.evaluation.pid,
                            {"status_before": "Pending", "status_after": "Processing"})

    async def test_a_failed_plugin_fails_its_evaluation(self):
        await self._start()
        res = await self._as_the_worker("patch", f"/evaluations/{self.evaluation.pid}/plugins/"
                                                 f"{self.evaluation_plugin.pid}/fail", json={"error_message": "boom"})
        self.assertEqual(res.status_code, 200, res.text)
        _, plugin_failed, failed = await events()
        self.assertEqual(plugin_failed["details"]["status_after"], "Failed")
        self.assertEqual(failed["details"], {"status_before": "Pending", "status_after": "Failed"})
        self.assertNotIn("boom", json.dumps(plugin_failed))

    async def test_recording_measures(self):
        await self._start()
        body = {str(self.evaluation_plugin.pid): [{"name": "Accuracy", "score": 0.9, "time": "2026-10-18T10:00:00Z"},
                                                  {"name": "F1", "score": 0.8, "time": "2026-10-18T10:00:00Z"}]}
        res = await self._as_the_worker("post", f"/evaluations/{self.evaluation.pid}/measures", json=body)
        self.assertEqual(res.status_code, 201, res.text)
        _, recorded = await events()
        self.assertRunEvent(recorded, "engine.measures.recorded", "evaluation", self.evaluation.pid, {"count": 2})

    async def test_uploading_an_artifact(self):
        from aisc_backend.models.artifact import Artifact
        from aisc_backend.routers import internal

        await self._start()
        upload = SimpleUploadedFile("plugin_execution.log", b"log body", content_type="text/plain")
        with mock.patch.object(internal.file_repository, "upload_file", return_value=True):
            res = await self._as_the_worker("post", f"/evaluations/{self.evaluation.pid}/artifacts",
                                            data={"evaluation_plugin_uuid": str(self.evaluation_plugin.pid)},
                                            FILES={"file": upload})
        self.assertEqual(res.status_code, 200, res.text)
        artifact = await Artifact.objects.aget(data=res.json()["file_name"])
        _, uploaded = await events()
        self.assertRunEvent(uploaded, "engine.artifact.uploaded", "artifact", artifact.pid, {"size": 8})

    async def test_marking_a_run_local_names_the_input_and_the_file_only(self):
        from aisc_backend.models import EvaluationInput

        with unrecorded():
            model = await AIComponent.objects.acreate(component_type=AIComponentType.MODEL, system=self.system,
                                                      name="model.onnx", description="", data="")
            evaluation_input = await EvaluationInput.objects.acreate(
                evaluation_plugin=self.evaluation_plugin, name="model", component=model,
                value={"local_model": {"file": "model.onnx"}})
        await self._start()
        marker = {"local_model": {"file": "model.onnx", "sha256": "ab" * 32}}
        res = await self._as_the_worker("patch", f"/evaluations/{self.evaluation.pid}/inputs/"
                                                 f"{evaluation_input.pid}/value", json={"value": marker})
        self.assertEqual(res.status_code, 200, res.text)
        _, marked = await events()
        self.assertRunEvent(marked, "engine.evaluation.input_marked_local", "evaluation", self.evaluation.pid,
                            {"input": "model", "file": "model.onnx", "sha256": "ab" * 32})
        self.assertNotIn(INTERNAL_SECRET, json.dumps(marked))

    async def test_a_run_whose_start_was_not_recorded_records_nothing(self):
        with unrecorded():
            await self._start()
        res = await self._as_the_worker("put", f"/evaluations/{self.evaluation.pid}?status=Processing")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(await events(), [])


class _FakePlugin:
    display_name = "Demo"
    project_config_definitions = []
    help_text = ""

    def on_config_change(self, config):
        return config, {}, {}


class Plugins(Recording):
    PACKAGE = {"package_name": "demo-pkg", "version": "0.1.0"}

    def setUp(self):
        super().setUp()
        from aisc_backend.routers.plugin import router

        self.plugins = TestAsyncClient(router)
        self.body = {**self.PACKAGE, "project_uuid": str(self.project.pid)}

    async def _install(self):
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.load_package",
                        return_value={"DemoA": _FakePlugin(), "DemoB": _FakePlugin()}):
            return await self.plugins.post("", json=self.body)

    async def test_installing_a_package_is_one_event_whatever_it_holds(self):
        res = await self._install()
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events()
        self.assertEvent(event, "plugin.installed", "plugin", "demo-pkg",
                         {"package": "demo-pkg", "version": "0.1.0", "reused": False})
        await self._install()
        self.assertEqual((await events())[1]["details"]["reused"], True)

    async def test_refreshing_a_package(self):
        await self._install()
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.refresh_package",
                        return_value={"DemoA": _FakePlugin()}):
            res = await self.plugins.post("/refresh", json=self.body)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEvent((await events())[-1], "plugin.refreshed", "plugin", "demo-pkg",
                         {"package": "demo-pkg", "version": "0.1.0"})

    async def test_removing_a_package(self):
        await self._install()
        res = await self.plugins.delete("", json=self.body)
        self.assertEqual(res.status_code, 204, res.text)
        self.assertEvent((await events())[-1], "plugin.removed", "plugin", "demo-pkg",
                         {"package": "demo-pkg", "version": "0.1.0"})

    async def test_switching_a_plugin_off(self):
        pid = (await self._install()).json()[0]["pid"]
        res = await self.plugins.patch(f"/{pid}/enabled", json={"enabled": False})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEvent((await events())[-1], "engine.plugin.toggled", "plugin", pid, {"enabled": False})

    async def test_configuring_a_plugin_and_restoring_an_earlier_configuration(self):
        pid = (await self._install()).json()[0]["pid"]
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.load_plugin", return_value=_FakePlugin()):
            first = await self.plugins.post(f"/{pid}/config", json={"config": {"threshold": 0.5}})
            self.assertEqual(first.status_code, 200, first.text)
            await self.plugins.post(f"/{pid}/config", json={"config": {"threshold": 0.9}})
        config_id = first.json()["plugin_config_id"]
        configured = (await events())[-1]
        self.assertEvent(configured, "engine.plugin.configured", "plugin", pid, {})
        self.assertEqual(configured["content"]["config"], {"threshold": 0.9})
        res = await self.plugins.post(f"/{pid}/configs/{config_id}/restore")
        self.assertEqual(res.status_code, 200, res.text)
        restored = (await events())[-1]
        self.assertEvent(restored, "engine.plugin.configured", "plugin", pid, {})
        self.assertEqual(restored["content"], {"config_id": config_id, "config": {"threshold": 0.5}, "settings": {}})
