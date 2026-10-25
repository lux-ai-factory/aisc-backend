"""
Reading one evaluation through the public and the internal route.

The detail body always lists the evaluation's plugins, whatever `include` asks for, and the body is the
same on both routes. An unknown evaluation answers 404 from the route itself. The number of SQL queries
for one read does not grow with the number of plugins, and reading without `include` costs the same as
reading with `include=plugin`.
"""
import os
import uuid
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from ninja.testing import TestAsyncClient

from aisc_backend.models import (
    AIComponent,
    AIComponentType,
    AISystem,
    Evaluation,
    EvaluationInput,
    EvaluationPlugin,
    EvaluationStatus,
    Plugin,
    PluginConfig,
    Project,
    ProjectStatus,
)
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.internal import router as internal_router

public_client = TestAsyncClient(evaluation_router)
internal_client = TestAsyncClient(internal_router)

INTERNAL_SECRET = "test-internal-secret"
INTERNAL_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}

TOP_LEVEL_KEYS = {"id", "pid", "created_at", "status", "project", "task", "system_id", "evaluation_plugins"}
PROJECT_KEYS = {"name", "pid"}
EVALUATION_PLUGIN_KEYS = {
    "id", "pid", "created_at", "description", "display_name", "error_message", "evaluation",
    "evaluation_inputs", "finished_at", "name", "package_name", "plugin_config", "plugin_pid",
    "started_at", "status", "version",
}
EVALUATION_INPUT_KEYS = {"name", "input_type", "input_file", "value"}

INCLUDE_QUERIES = ["", "?include=plugin", "?include=project,plugin", "?include=project,dataset,model,datashape,plugin"]
NOT_FOUND_BODY = {"detail": "Evaluation not found"}


def internal_env():
    return patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET})


class EvaluationDetailsTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        system = AISystem.objects.create(project=self.project)
        self.source = AIComponent.objects.create(
            name="source", description="", component_type=AIComponentType.DATASET, system=system,
        )
        self.shape = AIComponent.objects.create(
            name="shape", description="", component_type=AIComponentType.DATASHAPE, system=system,
            source_dataset=self.source,
        )
        self.plugin_a = self._plugin("PluginA")
        self.plugin_b = self._plugin("PluginB")

        self.e2 = self._evaluation([self.plugin_a, self.plugin_b])
        self.e1 = self._evaluation([self.plugin_a])
        self.e0 = self._evaluation([])
        self.unknown = uuid.uuid4()

    def _plugin(self, name):
        return Plugin.objects.create(
            name=name,
            display_name=f"{name} display",
            description="",
            package_name=f"{name.lower()}-pkg",
            version="0.1.0",
            project=self.project,
        )

    def _evaluation(self, plugins):
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Pending, project=self.project)
        for plugin in plugins:
            config = PluginConfig.objects.create(plugin=plugin, config={})
            evaluation_plugin = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config)
            EvaluationInput.objects.create(
                evaluation_plugin=evaluation_plugin, name="datashape", component=self.shape, value={},
            )
        return evaluation

    async def _public(self, path):
        return await public_client.get(path)

    async def _internal(self, path, with_secret=True):
        with internal_env():
            return await internal_client.get(path, headers=INTERNAL_HEADERS if with_secret else {})

    def _count_queries(self, client, path, headers=None):
        with internal_env(), CaptureQueriesContext(connection) as queries:
            response = async_to_sync(client.get)(path, headers=headers or {})
        self.assertEqual(200, response.status_code)
        return len(queries.captured_queries)

    # A read without include lists the plugins

    async def test_public_read_without_include_lists_the_plugins(self):
        response = await self._public(f"/{self.e2.pid}")

        self.assertEqual(200, response.status_code)
        self.assertIsInstance(response.json()["evaluation_plugins"], list)
        self.assertEqual(2, len(response.json()["evaluation_plugins"]))

    async def test_internal_read_without_include_lists_the_plugins(self):
        response = await self._internal(f"/evaluations/{self.e2.pid}")

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(response.json()["evaluation_plugins"]))

    async def test_public_read_with_empty_include_lists_the_plugins(self):
        response = await self._public(f"/{self.e2.pid}?include=")

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(response.json()["evaluation_plugins"]))

    async def test_public_read_of_evaluation_without_plugins_gives_empty_list(self):
        response = await self._public(f"/{self.e0.pid}")

        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["evaluation_plugins"])

    async def test_internal_read_of_evaluation_without_plugins_gives_empty_list(self):
        response = await self._internal(f"/evaluations/{self.e0.pid}")

        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["evaluation_plugins"])

    # Body shape

    async def test_top_level_shape_of_the_body(self):
        response = await self._public(f"/{self.e2.pid}")

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(TOP_LEVEL_KEYS, set(body))
        self.assertEqual({"name": "p", "pid": str(self.project.pid)}, body["project"])
        self.assertEqual(str(self.e2.pid), body["pid"])
        self.assertEqual("Pending", body["status"])

    async def test_shape_of_each_plugin_and_its_input(self):
        response = await self._public(f"/{self.e2.pid}")

        self.assertEqual(200, response.status_code)
        plugins = response.json()["evaluation_plugins"]
        self.assertEqual({"PluginA", "PluginB"}, {x["name"] for x in plugins})
        plugin_pids = {"PluginA": str(self.plugin_a.pid), "PluginB": str(self.plugin_b.pid)}
        for entry in plugins:
            self.assertEqual(EVALUATION_PLUGIN_KEYS, set(entry))
            self.assertEqual(plugin_pids[entry["name"]], entry["plugin_pid"])
            evaluation_input = entry["evaluation_inputs"][0]
            self.assertEqual(EVALUATION_INPUT_KEYS, set(evaluation_input))
            self.assertEqual("datashape", evaluation_input["input_type"])
            self.assertEqual(str(self.shape.pid), evaluation_input["input_file"]["pid"])
            self.assertEqual(str(self.source.pid), evaluation_input["input_file"]["source_dataset_pid"])

    # The body does not depend on include nor on the route

    def _assert_reference_body(self, body):
        """The include=project,plugin body callers read today: full shape, both plugins."""
        self.assertEqual(TOP_LEVEL_KEYS, set(body))
        self.assertEqual(PROJECT_KEYS, set(body["project"]))
        self.assertEqual(2, len(body["evaluation_plugins"]))
        for entry in body["evaluation_plugins"]:
            self.assertEqual(EVALUATION_PLUGIN_KEYS, set(entry))
            self.assertEqual(EVALUATION_INPUT_KEYS, set(entry["evaluation_inputs"][0]))
            self.assertIn("source_dataset_pid", entry["evaluation_inputs"][0]["input_file"])

    async def test_public_body_is_the_same_for_every_include(self):
        bodies = {}
        for query in INCLUDE_QUERIES:
            response = await self._public(f"/{self.e2.pid}{query}")
            self.assertEqual(200, response.status_code, query)
            bodies[query] = response.json()

        reference = bodies["?include=project,plugin"]
        self._assert_reference_body(reference)
        for query, body in bodies.items():
            self.assertEqual(reference, body, query)

    async def test_internal_body_is_the_same_for_every_include_and_equals_the_public_one(self):
        bodies = {}
        for query in INCLUDE_QUERIES:
            response = await self._internal(f"/evaluations/{self.e2.pid}{query}")
            self.assertEqual(200, response.status_code, query)
            bodies[query] = response.json()

        public_response = await self._public(f"/{self.e2.pid}?include=project,plugin")
        self.assertEqual(200, public_response.status_code)
        public_reference = public_response.json()
        self._assert_reference_body(public_reference)

        reference = bodies["?include=project,plugin"]
        for query, body in bodies.items():
            self.assertEqual(reference, body, query)
        self.assertEqual(public_reference, reference)

    # Unknown, malformed, unauthenticated

    async def test_public_unknown_evaluation_answers_404(self):
        response = await self._public(f"/{self.unknown}")

        self.assertEqual(404, response.status_code)
        self.assertEqual(NOT_FOUND_BODY, response.json())

    async def test_internal_unknown_evaluation_answers_404(self):
        response = await self._internal(f"/evaluations/{self.unknown}")

        self.assertEqual(404, response.status_code)
        self.assertEqual(NOT_FOUND_BODY, response.json())

    async def test_public_unknown_evaluation_with_include_answers_404(self):
        response = await self._public(f"/{self.unknown}?include=project,plugin")

        self.assertEqual(404, response.status_code)
        self.assertEqual(NOT_FOUND_BODY, response.json())

    async def test_internal_unknown_evaluation_without_secret_answers_401(self):
        response = await self._internal(f"/evaluations/{self.unknown}", with_secret=False)

        self.assertEqual(401, response.status_code)

    async def test_public_malformed_pid_answers_422(self):
        response = await self._public("/not-a-uuid")

        self.assertEqual(422, response.status_code)

    # Query counts (sync: CaptureQueriesContext cannot be entered inside an async test)

    def test_public_read_without_include_costs_the_same_as_include_plugin(self):
        bare = self._count_queries(public_client, f"/{self.e2.pid}")
        with_plugin = self._count_queries(public_client, f"/{self.e2.pid}?include=plugin")

        self.assertEqual(with_plugin, bare)

    def test_public_read_with_include_plugin_does_not_grow_with_plugins(self):
        one_plugin = self._count_queries(public_client, f"/{self.e1.pid}?include=plugin")
        two_plugins = self._count_queries(public_client, f"/{self.e2.pid}?include=plugin")

        self.assertEqual(one_plugin, two_plugins)

    def test_public_read_without_include_does_not_grow_with_plugins(self):
        one_plugin = self._count_queries(public_client, f"/{self.e1.pid}")
        two_plugins = self._count_queries(public_client, f"/{self.e2.pid}")

        self.assertEqual(one_plugin, two_plugins)

    def test_internal_read_without_include_costs_the_same_as_include_project_plugin(self):
        bare = self._count_queries(internal_client, f"/evaluations/{self.e2.pid}", INTERNAL_HEADERS)
        with_plugin = self._count_queries(
            internal_client, f"/evaluations/{self.e2.pid}?include=project,plugin", INTERNAL_HEADERS,
        )

        self.assertEqual(with_plugin, bare)
