"""The engine's place in the one database.

The platform names a project and the system under assessment; qualification
describes that system and the engine tests it. For a result to be readable next
to what was qualified, the engine's rows have to point at those same two names:
a project belongs to a platform project, and an evaluation is of a system.

The link is on the evaluation rather than the model, because a measurement
never references a model: what is run, and therefore what is reported, is an
evaluation.
"""

import uuid

import unittest.mock as mock

from aisc_backend.auth import keycloak

from django.test import TestCase

from aisc_backend.models import Evaluation, Project
from aisc_backend.repositories.project_repository import ProjectRepository

project_repository = ProjectRepository()

#: These tests are about the routes, not about Keycloak, and they send a token
#: that is not meant to verify. The switch is pinned off for the module so the
#: suite behaves the same wherever it runs: the container it is run in has
#: AUTH_ENABLED=true, and without this it would pass locally and fail there.
_auth_off = None


def setUpModule():
    global _auth_off
    _auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
    _auth_off.start()


def tearDownModule():
    _auth_off.stop()


# The project router names the bearer check, so a call needs a token. AUTH_ENABLED
# is off in the suite, so it is not verified and no role is read: what the
# header does is get past the deny-by-default check.
SIGNED_IN = {"Authorization": "Bearer development"}



class PlatformLinksTestCase(TestCase):

    async def test_a_project_can_name_the_platform_project_it_belongs_to(self):
        pid = uuid.uuid4()
        project = await project_repository.create("linked")
        project.platform_project_id = pid
        await Project.objects.filter(id=project.id).aupdate(platform_project_id=pid)

        again = await Project.objects.aget(id=project.id)
        self.assertEqual(pid, again.platform_project_id)

    async def test_a_project_without_one_is_still_a_project(self):
        # Projects made before the platform existed, and any made outside it,
        # have no platform project. That is an absent link, not an invalid row.
        project = await project_repository.create("unlinked")
        again = await Project.objects.aget(id=project.id)
        self.assertIsNone(again.platform_project_id)

    async def test_an_evaluation_names_the_system_it_ran_against(self):
        project = await project_repository.create("tested")
        system = uuid.uuid4()
        evaluation = await Evaluation.objects.acreate(
            status="Pending", project=project, system_id=system
        )

        again = await Evaluation.objects.aget(id=evaluation.id)
        self.assertEqual(system, again.system_id)

    async def test_an_evaluation_without_a_system_is_allowed_but_says_nothing(self):
        project = await project_repository.create("tested")
        evaluation = await Evaluation.objects.acreate(status="Pending", project=project)
        again = await Evaluation.objects.aget(id=evaluation.id)
        self.assertIsNone(again.system_id)


class CreatingAProjectUnderThePlatformTestCase(TestCase):
    """A workspace made from a project on the launcher belongs to it."""

    async def test_a_new_project_can_be_created_under_a_platform_project(self):
        from ninja.testing import TestAsyncClient

        from aisc_backend.routers.project import router

        client = TestAsyncClient(router)
        pid = str(uuid.uuid4())
        response = await client.post("", json={"name": "from the launcher",
                                               "platform_project_id": pid},
                                     headers=SIGNED_IN)

        self.assertEqual(200, response.status_code)
        stored = await Project.objects.aget(name="from the launcher")
        self.assertEqual(uuid.UUID(pid), stored.platform_project_id)

    async def test_a_project_created_without_one_still_works(self):
        from ninja.testing import TestAsyncClient

        from aisc_backend.routers.project import router

        client = TestAsyncClient(router)
        response = await client.post("", json={"name": "on its own"}, headers=SIGNED_IN)

        self.assertEqual(200, response.status_code)
        stored = await Project.objects.aget(name="on its own")
        self.assertIsNone(stored.platform_project_id)


class ListingWorkspacesOfOneProjectTestCase(TestCase):
    """The project is chosen once, on the launcher, and the engine shows that
    project's workspaces. Listing every workspace of every project would be a
    second place where the choice is made."""

    async def _listed(self, query: str = "") -> list[str]:
        from ninja.testing import TestAsyncClient

        from aisc_backend.routers.project import router

        response = await TestAsyncClient(router).get(f"?{query}" if query else "", headers=SIGNED_IN)
        self.assertEqual(200, response.status_code)
        return [row["name"] for row in response.data]

    async def test_only_the_workspaces_of_the_project_asked_for(self):
        mine = uuid.uuid4()
        await project_repository.create("mine", mine)
        await project_repository.create("another", uuid.uuid4())
        await project_repository.create("no project at all")

        listed = await self._listed(f"platform_project_id={mine}")

        self.assertEqual(["mine"], listed)

    async def test_asking_for_none_of_them_lists_all_of_them(self):
        # The engine on its own, with no launcher in front of it, is still a
        # usable application: it shows what it has.
        await project_repository.create("mine", uuid.uuid4())
        await project_repository.create("no project at all")

        self.assertEqual(2, len(await self._listed()))

    async def test_a_project_with_nothing_in_it_lists_nothing(self):
        await project_repository.create("someone else's", uuid.uuid4())

        self.assertEqual([], await self._listed(f"platform_project_id={uuid.uuid4()}"))


class TheEngineInheritsTheProjectTestCase(TestCase):
    """There is no workspace to create in the engine.

    The project is chosen once, on the launcher. Opening the engine inside it
    means working on it: the engine resolves its own row for that project,
    making it the first time and finding it every time after. Nobody is asked
    to name anything, and there is at most one of them per project.
    """

    async def _resolve(self, platform_project_id) -> dict:
        from ninja.testing import TestAsyncClient

        from aisc_backend.routers.project import router

        response = await TestAsyncClient(router).post(
            f"/for-platform/{platform_project_id}", headers=SIGNED_IN
        )
        self.assertEqual(200, response.status_code, response.content)
        return response.data

    async def test_the_first_visit_makes_it(self):
        pid = uuid.uuid4()

        resolved = await self._resolve(pid)

        stored = await Project.objects.aget(pid=resolved["pid"])
        self.assertEqual(pid, stored.platform_project_id)

    async def test_every_visit_after_that_finds_the_same_one(self):
        pid = uuid.uuid4()

        first = await self._resolve(pid)
        again = await self._resolve(pid)

        self.assertEqual(first["pid"], again["pid"])
        self.assertEqual(1, await Project.objects.filter(platform_project_id=pid).acount())

    async def test_two_projects_each_get_their_own(self):
        a, b = uuid.uuid4(), uuid.uuid4()

        first = await self._resolve(a)
        second = await self._resolve(b)

        self.assertNotEqual(first["pid"], second["pid"])
