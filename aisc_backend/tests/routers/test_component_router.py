from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType, ProjectConfig, ProjectConfigCategory
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router


client = TestAsyncClient(router)


class ComponentRouterTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")

    async def _component(self, component_type, **kw):
        defaults = dict(name=component_type, description="", data="")
        defaults.update(kw)
        return await AIComponent.objects.acreate(
            component_type=component_type, system=self.system, **defaults
        )

    async def test_get_component_roundtrip(self):
        comp = await self._component(AIComponentType.DATASET)
        res = await client.get(f"/{comp.pid}")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["pid"], str(comp.pid))
        self.assertEqual(body["component_type"], "dataset")

    async def test_get_missing_component_404(self):
        res = await client.get("/00000000-0000-0000-0000-000000000000")
        self.assertEqual(res.status_code, 404)

    async def test_update_component_typed_json_value(self):
        comp = await self._component(AIComponentType.LLM)
        res = await client.patch(
            f"/{comp.pid}",
            json={"name": "renamed", "json_value": {"endpoint_url": "http://example.com", "secret_key": "k"}},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["name"], "renamed")
        self.assertEqual(body["json_value"], {"endpoint_url": "http://example.com", "secret_key": "k"})

    async def test_update_source_dataset_must_be_dataset(self):
        ds = await self._component(AIComponentType.DATASET)
        model = await self._component(AIComponentType.MODEL)
        shape = await self._component(AIComponentType.DATASHAPE)

        res = await client.patch(f"/{shape.pid}", json={"source_dataset_pid": str(model.pid)})
        self.assertEqual(res.status_code, 400)

        res = await client.patch(f"/{shape.pid}", json={"source_dataset_pid": str(ds.pid)})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["source_dataset_pid"], str(ds.pid))

    async def test_models_requires_llm(self):
        comp = await self._component(AIComponentType.MODEL)
        res = await client.get(f"/{comp.pid}/models")
        self.assertEqual(res.status_code, 400)

    async def test_models_llm_without_endpoint_configured(self):
        comp = await self._component(AIComponentType.LLM, json_value={})
        res = await client.get(f"/{comp.pid}/models")
        self.assertEqual(res.status_code, 400)

    async def test_models_llm_without_secret_configured(self):
        comp = await self._component(
            AIComponentType.LLM, json_value={"endpoint_url": "http://example.com"}
        )
        res = await client.get(f"/{comp.pid}/models")
        self.assertEqual(res.status_code, 400)

    async def test_models_llm_lists_models(self):
        await ProjectConfig.objects.acreate(
            project=self.project, name="Key", key="k",
            category=ProjectConfigCategory.SECRETS, encrypted_value="cipher",
        )
        comp = await self._component(
            AIComponentType.LLM,
            json_value={"endpoint_url": "http://example.com", "secret_key": "k"},
        )
        with (
            patch("aisc_backend.routers.component.list_openai_models", return_value=["gpt-4o"]),
            patch("aisc_backend.routers.component.decrypt_value", return_value="sk-test"),
        ):
            res = await client.get(f"/{comp.pid}/models")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["models"], ["gpt-4o"])

    async def test_delete_component(self):
        comp = await self._component(AIComponentType.MODEL)
        res = await client.delete(f"/{comp.pid}")
        self.assertEqual(res.status_code, 204)

    async def test_delete_missing_component_404(self):
        res = await client.delete("/00000000-0000-0000-0000-000000000000")
        self.assertEqual(res.status_code, 404)
