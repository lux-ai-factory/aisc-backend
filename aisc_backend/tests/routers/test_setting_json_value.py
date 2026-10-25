"""A project setting's json_value must be a JSON object (or null, or absent).

A string, number, boolean or list used to slip through as {"type": "json", "value": None}; it is now
a validation error, on create and on update, and the stored setting is left as it was.
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.project_config import router

client = TestAsyncClient(router)

NOT_AN_OBJECT = "json_value must be an object"
THRESHOLD = {"type": "number", "value": 0.5}


class SettingJsonValueTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)

    async def _create_threshold(self):
        response = await client.post(
            f"/{self.project.pid}",
            json={"category": "variables", "key": "threshold", "name": "Threshold", "json_value": THRESHOLD},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["pid"]

    def assertRefusedAsNotAnObject(self, response):
        self.assertEqual(422, response.status_code, response.text)
        error = response.json()["detail"][0]
        self.assertEqual(["body", "data", "json_value"], error["loc"])
        self.assertTrue(error["msg"].endswith(NOT_AN_OBJECT), error["msg"])

    async def test_update_with_a_non_object_json_value_is_refused(self):
        pid = await self._create_threshold()
        for value in ("notadict", "", 5, True, [1, 2]):
            with self.subTest(value=value):
                response = await client.patch(f"/{self.project.pid}/{pid}", json={"json_value": value})
                self.assertRefusedAsNotAnObject(response)

        listing = await client.get(f"/{self.project.pid}")
        self.assertEqual(200, listing.status_code)
        [setting] = [s for s in listing.json() if s["pid"] == pid]
        self.assertEqual(THRESHOLD, setting["json_value"])

    async def test_create_with_a_non_object_json_value_is_refused(self):
        response = await client.post(
            f"/{self.project.pid}",
            json={"category": "variables", "key": "k", "name": "K", "json_value": "notadict"},
        )
        self.assertRefusedAsNotAnObject(response)

        listing = await client.get(f"/{self.project.pid}")
        self.assertEqual(200, listing.status_code)
        self.assertEqual(0, len(listing.json()))

    async def test_update_with_an_empty_object_json_value_is_kept_as_today(self):
        pid = await self._create_threshold()
        response = await client.patch(f"/{self.project.pid}/{pid}", json={"json_value": {}})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"type": "json", "value": None}, response.json()["json_value"])

    async def test_update_with_a_null_json_value_keeps_the_value(self):
        pid = await self._create_threshold()
        response = await client.patch(f"/{self.project.pid}/{pid}", json={"name": "Renamed", "json_value": None})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("Renamed", response.json()["name"])
        self.assertEqual(THRESHOLD, response.json()["json_value"])
