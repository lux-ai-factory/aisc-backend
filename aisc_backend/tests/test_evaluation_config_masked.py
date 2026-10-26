"""The public evaluation routes answer each run's plugin configuration masked; the worker's route answers it raw.

GET /api/v1/evaluations/{pid} and GET /api/v1/projects/{pid}/evaluations show
`evaluation_plugins[].plugin_config.config` as every other read route shows a configuration: a key field "set"
when it holds a value and null when empty, each kept `config.*` path "set", a key-shaped string "set". The
worker's GET /internal/evaluations/{pid} answers the stored configuration as it is.
"""
import os
from unittest import mock

from ninja.testing import TestAsyncClient

from aisc_backend.models import Evaluation, EvaluationPlugin, EvaluationStatus
from aisc_backend.tests.test_ledger_events import INTERNAL_SECRET, unrecorded
from aisc_backend.tests.test_project_plugin_config_route import CRED, LOAD_PLUGIN, _Base, _Form

STORED = {"threshold": 0.5, "model_credential": CRED, "endpoint": "https://user:pa55word@example.invalid/v1"}
SHOWN = {"threshold": 0.5, "model_credential": "set", "endpoint": "set"}


class EvaluationConfigMasked(_Base):
    def setUp(self):
        super().setUp()
        from aisc_backend.routers.evaluation import router as evaluation_router
        from aisc_backend.routers.internal import router as internal_router

        self.evaluations = TestAsyncClient(evaluation_router)
        self.internal = TestAsyncClient(internal_router)

    async def _evaluation(self, config, mappings=()):
        plugin = await self._plugin()
        row = await self._config(plugin, config, mappings)
        with unrecorded():
            evaluation = await Evaluation.objects.acreate(status=EvaluationStatus.Pending, project=self.project)
            await EvaluationPlugin.objects.acreate(evaluation=evaluation, plugin_config=row)
        return evaluation

    async def _public(self, evaluation, query="", form=None):
        with mock.patch(LOAD_PLUGIN, return_value=form or _Form()):
            return await self.evaluations.get(f"/{evaluation.pid}{query}")

    async def _listed(self, evaluation, form=None):
        with mock.patch(LOAD_PLUGIN, return_value=form or _Form()):
            res = await self.projects.get(f"/{self.project.pid}/evaluations")
        self.assertEqual(res.status_code, 200, res.text)
        [entry] = [e for e in res.json() if e["pid"] == str(evaluation.pid)]
        return res, entry

    async def _internal(self, evaluation):
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}), \
                mock.patch(LOAD_PLUGIN, return_value=_Form()):
            return await self.internal.get(f"/evaluations/{evaluation.pid}",
                                           headers={"X-Internal-Secret": INTERNAL_SECRET})

    @staticmethod
    def _config_of(body):
        [entry] = body["evaluation_plugins"]
        return entry["plugin_config"]

    async def test_the_public_detail_masks_the_keys(self):
        evaluation = await self._evaluation(STORED)
        res = await self._public(evaluation)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._config_of(res.json())["config"], SHOWN)
        self.assertNotIn(CRED, res.text)
        self.assertNotIn("pa55word", res.text)

    async def test_the_public_detail_with_include_is_the_same(self):
        evaluation = await self._evaluation(STORED)
        bare = await self._public(evaluation)
        included = await self._public(evaluation, "?include=project,plugin")
        self.assertEqual(included.status_code, 200, included.text)
        self.assertEqual(included.json(), bare.json())

    async def test_the_projects_evaluation_list_masks_the_keys(self):
        evaluation = await self._evaluation(STORED)
        res, entry = await self._listed(evaluation)
        self.assertEqual(self._config_of(entry)["config"], SHOWN)
        self.assertNotIn(CRED, res.text)
        self.assertNotIn("pa55word", res.text)

    async def test_the_workers_route_answers_the_stored_configuration(self):
        evaluation = await self._evaluation(STORED)
        res = await self._internal(evaluation)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._config_of(res.json())["config"], STORED)

    async def test_a_kept_path_is_set_and_its_mapping_listed_on_every_route(self):
        secret = await self._secret(key="demoa_api_key", name="Demo: api_key")
        evaluation = await self._evaluation({"service_url": "https://example.invalid"},
                                            [("config.api_key", secret)])
        selections = [{"plugin_config_key": "config.api_key", "project_config_pid": str(secret.pid)}]

        public = self._config_of((await self._public(evaluation)).json())
        self.assertEqual(public["config"], {"service_url": "https://example.invalid", "api_key": "set"})
        self.assertEqual(public["project_config_selections"], selections)

        _, entry = await self._listed(evaluation)
        self.assertEqual(self._config_of(entry)["config"],
                         {"service_url": "https://example.invalid", "api_key": "set"})
        self.assertEqual(self._config_of(entry)["project_config_selections"], selections)

        internal = self._config_of((await self._internal(evaluation)).json())
        self.assertEqual(internal["config"], {"service_url": "https://example.invalid"})
        self.assertEqual(internal["project_config_selections"], selections)

    async def test_a_plugin_that_cannot_be_loaded_is_masked_by_name_and_value(self):
        evaluation = await self._evaluation({"n": 1, "judge_api_key": CRED})
        with mock.patch(LOAD_PLUGIN, side_effect=RuntimeError("cannot load")):
            res = await self.evaluations.get(f"/{evaluation.pid}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._config_of(res.json())["config"], {"n": 1, "judge_api_key": "set"})
        self.assertNotIn(CRED, res.text)

    async def test_an_empty_key_field_is_null(self):
        evaluation = await self._evaluation({"threshold": 0.5, "model_credential": ""})
        res = await self._public(evaluation)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._config_of(res.json())["config"], {"threshold": 0.5, "model_credential": None})
