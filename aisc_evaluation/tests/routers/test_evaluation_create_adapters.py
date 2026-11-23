"""
Evaluation create: the adapter is defaulted per slot and ``adapter_required`` is
enforced (ADR 0009).

The plugin loader and the celery dispatch are mocked, so this exercises the
router + ORM path only. The chosen adapter, its version and the endpoint
provenance land on the ``EvaluationInput``.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.ai_system import AIComponent, AIComponentType
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_evaluation.models import Evaluation, EvaluationInput
from aisc_evaluation.routers.evaluation import router
from aisc_plugin_interface.models.evaluation_input import InputDefinition, InputType
from aisc_plugins.models import Plugin, PluginConfig


client = TestAsyncClient(router)


def _slot(name, accepts, *, adapter=None, adapter_required=False):
    return InputDefinition(
        name=name, label=name.title(), input_type=InputType.LLM, accepts=accepts,
        adapter=adapter, adapter_required=adapter_required,
    )


class EvaluationCreateAdapterTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready)
        ProjectMember.objects.create(
            project=self.project, subject="development", role=MembershipRole.Owner
        )

    async def _plugin(self):
        plugin = await Plugin.objects.acreate(
            name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
            version="0.1.0", project=self.project,
        )
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        plugin.current_config = config
        await plugin.asave()
        return plugin

    async def _component(self, **fields):
        return await AIComponent.objects.acreate(
            project=self.project, name=fields.pop("name", "comp"), description="",
            data="", **fields,
        )

    def _patch(self, definitions):
        return (
            patch(
                "aisc_plugins.routers.plugin.plugin_loader.load_plugin",
                return_value=SimpleNamespace(
                    input_definitions=definitions, project_config_definitions=[]
                ),
            ),
            patch(
                "aisc_backend.services.celery_service.run_evaluation",
                new=AsyncMock(return_value=SimpleNamespace(task_id=str(uuid.uuid4()))),
            ),
        )

    async def _create(self, component, *, slot, adapter=None):
        plugin = await self._plugin()
        payload = {
            "project_pid": str(self.project.pid),
            "plugins_to_run": [{
                "name": "DemoPlugin",
                "inputs": [{"pid": str(component.pid), "name": "target",
                            "adapter": adapter}],
            }],
        }
        load_patch, run_patch = self._patch([slot])
        with load_patch, run_patch:
            response = await client.post("/task", json=payload)
        return response

    async def test_the_adapter_is_defaulted_from_the_slot(self):
        component = await self._component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            adapter="openai", url="https://models.example/v1",
        )
        response = await self._create(component, slot=_slot("target", ["openai-chat"]))
        self.assertEqual(200, response.status_code, response.text)
        inp = await EvaluationInput.objects.select_related("evaluation_plugin__evaluation").aget()
        self.assertEqual(inp.adapter, "openai")
        self.assertEqual(inp.adapter_version, "1")
        self.assertEqual(inp.provenance["url"], "https://models.example/v1")
        self.assertEqual(inp.provenance["adapter"], "openai")

    async def test_an_explicit_adapter_that_fits_wins(self):
        component = await self._component(
            component_type=AIComponentType.ENDPOINT, protocol="agent", adapter="a2a",
            url="https://agent.example",
        )
        response = await self._create(
            component, slot=_slot("target", ["openai-chat"]), adapter="a2a"
        )
        self.assertEqual(200, response.status_code, response.text)
        inp = await EvaluationInput.objects.aget()
        self.assertEqual(inp.adapter, "a2a")

    async def test_a_locked_slot_ignores_a_different_request(self):
        component = await self._component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            adapter="openai", url="https://models.example/v1",
        )
        response = await self._create(
            component,
            slot=_slot("target", ["openai-chat"], adapter="openai", adapter_required=True),
            adapter="a2a",
        )
        self.assertEqual(200, response.status_code, response.text)
        inp = await EvaluationInput.objects.aget()
        self.assertEqual(inp.adapter, "openai")

    async def test_an_adapter_that_does_not_fit_is_refused(self):
        component = await self._component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            adapter="openai", url="https://models.example/v1",
        )
        response = await self._create(
            component, slot=_slot("target", ["openai-chat"]), adapter="a2a"
        )
        self.assertEqual(400, response.status_code, response.text)
        self.assertFalse(await EvaluationInput.objects.aexists())

    async def test_a_component_from_another_project_is_not_found(self):
        other = await Project.objects.acreate(name="other", status=ProjectStatus.Ready)
        component = await AIComponent.objects.acreate(
            project=other, name="theirs", description="", data="",
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            adapter="openai", url="https://models.example/v1",
        )
        response = await self._create(component, slot=_slot("target", ["openai-chat"]))
        self.assertEqual(400, response.status_code, response.text)
