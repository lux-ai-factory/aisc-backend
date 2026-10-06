"""A project is found by any name, "/" included (code review 2026-10-06).

The engine names its project after the platform project, whose name is free text. Sean's
GET /projects/by-name/{name} takes one path segment, and the server decodes %2F before routing, so a name
with "/" could not be found (the webapp could not open such a project). GET /projects/by-name?name=...
(routers/project_by_name.py, mounted in front of /projects) takes the name as a query parameter, where every
character travels; the old route stays. Through the whole URL configuration, so the order of the routers is
tested too."""
import unittest
import unittest.mock as mock

from django.conf import settings
from django.test import TestCase

from aisc_backend.auth import keycloak
from aisc_backend.models import Project, ProjectStatus

NAMES = ["a/b", "a b", "a#b", "a?b&c=d", "plain"]
#: with authentication off any bearer passes (KeycloakAuth); ninja refuses a call without one
BEARER = {"Authorization": "Bearer development"}


@unittest.skipIf(getattr(settings, "AISC_DEPLOYMENT", None) == "configurator", "standalone: no door, no project header")
@mock.patch.object(keycloak, "AUTH_ENABLED", False)
class AProjectByAnyName(TestCase):
    async def test_every_name_is_found_by_the_query_route(self):
        for name in NAMES:
            await Project.objects.acreate(name=name, description="", status=ProjectStatus.Created)
        for name in NAMES:
            with self.subTest(name=name):
                found = await self.async_client.get("/api/v1/projects/by-name", {"name": name}, headers=BEARER)
                self.assertEqual(found.status_code, 200, found.content)
                self.assertEqual(found.json()["name"], name)

    async def test_the_old_route_still_finds_a_plain_name(self):
        await Project.objects.acreate(name="plain", description="", status=ProjectStatus.Created)
        found = await self.async_client.get("/api/v1/projects/by-name/plain", headers=BEARER)
        self.assertEqual((found.status_code, found.json()["name"]), (200, "plain"))
