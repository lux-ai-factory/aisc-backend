"""An LLM component's secret belongs to the address it was entered for. An edit that keeps the component's
secret_key while moving endpoint_url to another origin (scheme, host, port) is refused (422) unless the request
gives the secret again (secret_value), which is then the project secret's value. Any other edit is as before."""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType, ProjectConfig, ProjectConfigCategory
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router
from aisc_backend.utils.encryption import decrypt_value, encrypt_value

client = TestAsyncClient(router)
KEY = "sk-entered-for-one-example"
NEW_KEY = "sk-typed-again-for-two-example"
BEFORE = {"endpoint_url": "https://one.example.com/v1", "secret_key": "k"}


class KeyFollowsItsEndpoint(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.secret = ProjectConfig.objects.create(project=self.project, name="Key", key="k",
                                                   category=ProjectConfigCategory.SECRETS,
                                                   encrypted_value=encrypt_value(KEY))
        ProjectConfig.objects.create(project=self.project, name="Other", key="other",
                                     category=ProjectConfigCategory.SECRETS, encrypted_value=encrypt_value("x"))

    async def llm(self, json_value=None) -> AIComponent:
        return await AIComponent.objects.acreate(component_type=AIComponentType.LLM, system=self.system, name="llm",
                                                 description="", data="", json_value=dict(json_value or BEFORE))

    async def patch(self, comp, **body):
        return await client.patch(f"/{comp.pid}", json=body)

    async def stored_key(self) -> str:
        return decrypt_value((await ProjectConfig.objects.aget(pk=self.secret.pk)).encrypted_value)

    async def assert_refused(self, endpoint_url: str):
        comp = await self.llm()
        res = await self.patch(comp, json_value={"endpoint_url": endpoint_url, "secret_key": "k"})
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn("enter the key again", res.json()["detail"])
        await comp.arefresh_from_db()
        self.assertEqual(comp.json_value, BEFORE, "a refused edit changed the component")
        self.assertEqual(await self.stored_key(), KEY)

    async def test_another_host_with_the_same_secret_is_refused(self):
        await self.assert_refused("https://two.example.com/v1")

    async def test_another_port_with_the_same_secret_is_refused(self):
        await self.assert_refused("https://one.example.com:8443/v1")

    async def test_another_scheme_with_the_same_secret_is_refused(self):
        await self.assert_refused("http://one.example.com/v1")

    async def test_the_same_origin_keeps_the_secret(self):
        comp = await self.llm()
        moved = {"endpoint_url": "https://ONE.example.com:443/v2", "secret_key": "k"}
        res = await self.patch(comp, json_value=moved)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["json_value"], moved)

    async def test_the_secret_given_again_moves_the_component_and_is_the_secrets_value(self):
        comp = await self.llm()
        moved = {"endpoint_url": "https://two.example.com/v1", "secret_key": "k"}
        res = await self.patch(comp, json_value=moved, secret_value=NEW_KEY)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn(NEW_KEY, res.text)
        self.assertEqual(res.json()["json_value"], moved)
        self.assertEqual(await self.stored_key(), NEW_KEY)
        secret = await ProjectConfig.objects.aget(pk=self.secret.pk)
        self.assertNotIn(NEW_KEY, secret.masked_value)

    async def test_an_empty_secret_is_not_the_secret_given_again(self):
        comp = await self.llm()
        res = await self.patch(comp, json_value={"endpoint_url": "https://two.example.com/v1", "secret_key": "k"},
                               secret_value="  ")
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(await self.stored_key(), KEY)

    async def test_an_edit_that_keeps_the_endpoint_is_as_before(self):
        comp = await self.llm()
        res = await self.patch(comp, name="renamed")
        self.assertEqual(res.status_code, 200, res.text)
        res = await self.patch(comp, json_value=dict(BEFORE))
        self.assertEqual(res.status_code, 200, res.text)

    async def test_a_component_without_a_secret_moves_freely(self):
        comp = await self.llm({"endpoint_url": "https://one.example.com/v1"})
        res = await self.patch(comp, json_value={"endpoint_url": "https://two.example.com/v1"})
        self.assertEqual(res.status_code, 200, res.text)

    async def test_a_secret_given_for_no_move_still_needs_its_secret_to_exist(self):
        comp = await self.llm({"endpoint_url": "https://one.example.com/v1", "secret_key": "gone"})
        res = await self.patch(comp, json_value={"endpoint_url": "https://two.example.com/v1", "secret_key": "gone"},
                               secret_value=NEW_KEY)
        self.assertEqual(res.status_code, 400, res.text)
