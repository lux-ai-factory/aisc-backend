"""
Slot candidates: which components may fill a plugin slot, and with which adapter
(ADR 0009).

One compatibility rule decides every list: a slot's ``accepts`` against a
component's protocol/format. Deny-by-default project scoping means a second
project's components are never listed, whatever the slot.
"""
from types import SimpleNamespace

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.ai_system import AIComponent, AIComponentType
from aisc_backend.models.project import Project, ProjectStatus
from aisc_plugin_interface.models.evaluation_input import InputDefinition, InputType
from aisc_plugins.models import Plugin
from aisc_plugins.routers.plugin import router


client = TestAsyncClient(router)


def _slot(name, accepts, *, adapter=None, adapter_required=False, role="subject"):
    return InputDefinition(
        name=name, label=name.title(), input_type=InputType.LLM, required=True,
        accepts=accepts, role=role, adapter=adapter, adapter_required=adapter_required,
    )


class SlotCandidatesTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.other = Project.objects.create(name="other", status=ProjectStatus.Ready)
        self.plugin = Plugin.objects.create(
            name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
            version="0.1.0", project=self.project,
        )

    def _loader(self, definitions):
        return {
            "load_plugin": lambda *a, **k: SimpleNamespace(input_definitions=definitions),
            "list_adapters": lambda *a, **k: {},
        }

    async def test_llm_slot_lists_only_openai_chat_endpoints(self):
        import unittest.mock as mock
        await AIComponent.objects.acreate(
            project=self.project, name="judge", description="", data="",
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            adapter="openai",
        )
        await AIComponent.objects.acreate(
            project=self.project, name="agent", description="", data="",
            component_type=AIComponentType.ENDPOINT, protocol="agent", adapter="a2a",
        )
        await AIComponent.objects.acreate(
            project=self.project, name="dataset", description="", data="d.csv",
            component_type=AIComponentType.DATASET, format="csv",
        )
        # another project's endpoint must never appear
        await AIComponent.objects.acreate(
            project=self.other, name="theirs", description="", data="",
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            adapter="openai",
        )

        with mock.patch(
            "aisc_plugins.routers.plugin.plugin_loader",
            **self._loader([_slot("target", ["openai-chat"])]),
        ):
            res = await client.get(f"/{self.plugin.pid}/slots/target/candidates")
        self.assertEqual(200, res.status_code, res.text)
        body = res.json()
        self.assertEqual(body["accepts"], ["openai-chat"])
        # an A2A agent serves openai-chat too, so it is a candidate; the dataset
        # and the other project's endpoint are not.
        self.assertEqual(
            [c["name"] for c in body["candidates"]], ["agent", "judge"]
        )
        by_name = {c["name"]: c for c in body["candidates"]}
        self.assertEqual(by_name["judge"]["default_adapter"], "openai")
        self.assertEqual(by_name["agent"]["default_adapter"], "a2a")

    async def test_records_slot_lists_datasets_with_a_reading_adapter(self):
        import unittest.mock as mock
        await AIComponent.objects.acreate(
            project=self.project, name="csv", description="", data="d.csv",
            component_type=AIComponentType.DATASET, format="csv",
        )
        await AIComponent.objects.acreate(
            project=self.project, name="onnx", description="", data="m.onnx",
            component_type=AIComponentType.MODEL, format="onnx",
        )
        with mock.patch(
            "aisc_plugins.routers.plugin.plugin_loader",
            **self._loader([_slot("data", ["records"], role="input")]),
        ):
            res = await client.get(f"/{self.plugin.pid}/slots/data/candidates")
        self.assertEqual(200, res.status_code, res.text)
        candidates = {c["name"]: c for c in res.json()["candidates"]}
        self.assertEqual(set(candidates), {"csv"})
        self.assertEqual(candidates["csv"]["adapters"], ["csv"])

    async def test_a_datashape_fills_a_json_slot_with_no_adapter(self):
        import unittest.mock as mock
        await AIComponent.objects.acreate(
            project=self.project, name="shape", description="", data="",
            component_type=AIComponentType.DATASHAPE, json_value={"x": "int"},
        )
        with mock.patch(
            "aisc_plugins.routers.plugin.plugin_loader",
            **self._loader([_slot("shape", ["json"], role="input")]),
        ):
            res = await client.get(f"/{self.plugin.pid}/slots/shape/candidates")
        self.assertEqual(200, res.status_code, res.text)
        candidates = res.json()["candidates"]
        self.assertEqual([c["name"] for c in candidates], ["shape"])
        self.assertEqual(candidates[0]["adapters"], [])

    async def test_a_locked_slot_reports_adapter_required(self):
        import unittest.mock as mock
        with mock.patch(
            "aisc_plugins.routers.plugin.plugin_loader",
            **self._loader([_slot("target", ["openai-chat"], adapter="openai", adapter_required=True)]),
        ):
            res = await client.get(f"/{self.plugin.pid}/slots/target/candidates")
        self.assertEqual(200, res.status_code, res.text)
        body = res.json()
        self.assertTrue(body["adapter_required"])
        self.assertEqual(body["adapter"], "openai")

    async def test_unknown_slot_404(self):
        import unittest.mock as mock
        with mock.patch(
            "aisc_plugins.routers.plugin.plugin_loader",
            **self._loader([_slot("target", ["openai-chat"])]),
        ):
            res = await client.get(f"/{self.plugin.pid}/slots/nope/candidates")
        self.assertEqual(404, res.status_code)
