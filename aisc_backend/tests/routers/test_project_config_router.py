from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.project_config import router


client = TestAsyncClient(router)


class ProjectConfigRouterTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)

    async def test_create_variable_config_roundtrip(self):
        res = await client.post(
            f"/{self.project.pid}",
            json={
                "category": "variables",
                "key": "max_tokens",
                "name": "Max tokens",
                "json_value": {"type": "number", "value": 42},
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["category"], "variables")
        self.assertEqual(body["key"], "max_tokens")
        self.assertEqual(body["json_value"], {"type": "number", "value": 42})
        self.assertEqual(body["masked_value"], "")

        listing = await client.get(f"/{self.project.pid}")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 1)

    async def test_create_secret_requires_value(self):
        res = await client.post(
            f"/{self.project.pid}",
            json={"category": "secrets", "key": "api_key", "name": "API key", "value": ""},
        )
        self.assertEqual(res.status_code, 422)

    async def test_create_secret_encrypts_and_masks(self):
        res = await client.post(
            f"/{self.project.pid}",
            json={"category": "secrets", "key": "api_key", "name": "API key", "value": "sk-1234567890"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["masked_value"].startswith("sk-1..."))

    async def test_update_variable_json_value(self):
        create = await client.post(
            f"/{self.project.pid}",
            json={"category": "variables", "key": "threshold", "name": "Threshold", "json_value": {"type": "number", "value": 0.5}},
        )
        pid = create.json()["pid"]

        updated = await client.patch(
            f"/{self.project.pid}/{pid}",
            json={"json_value": {"type": "boolean", "value": True}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["json_value"], {"type": "boolean", "value": True})
        self.assertEqual(updated.json()["name"], "Threshold")

    async def test_update_secret_value(self):
        create = await client.post(
            f"/{self.project.pid}",
            json={"category": "secrets", "key": "token", "name": "Token", "value": "secret-value"},
        )
        pid = create.json()["pid"]

        updated = await client.patch(
            f"/{self.project.pid}/{pid}",
            json={"value": "new-secret-value-123"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertTrue(updated.json()["masked_value"].startswith("new-..."))
        self.assertTrue(updated.json()["masked_value"].endswith("-123"))

    async def test_update_secret_value_rejected_on_variable(self):
        create = await client.post(
            f"/{self.project.pid}",
            json={"category": "variables", "key": "mode", "name": "Mode", "json_value": {"type": "string", "value": "a"}},
        )
        pid = create.json()["pid"]

        res = await client.patch(f"/{self.project.pid}/{pid}", json={"value": "not a secret"})
        self.assertEqual(res.status_code, 400)

    async def test_delete_config(self):
        create = await client.post(
            f"/{self.project.pid}",
            json={"category": "variables", "key": "k", "name": "K", "json_value": {"type": "json", "value": {"a": 1}}},
        )
        pid = create.json()["pid"]
        res = await client.delete(f"/{self.project.pid}/{pid}")
        self.assertEqual(res.status_code, 204)
        listing = await client.get(f"/{self.project.pid}")
        self.assertEqual(len(listing.json()), 0)
