"""A component names only its own project's targets and connections.

A resource component whose value is `target:<pid>/<key>` or `connection:<pid>/<name>` is checked when it is
created (POST /api/v1/projects/{pid}/components) and when its value is changed (PATCH /api/v1/components/{pid}):
the pid must be the platform pid of the component's project. A component naming another project's target or
connection is refused with 400 and nothing is saved, audited or recorded in the ledger; other values are not
checked. Run as test_ledger_events.py runs the routes: the ledger on, the project admitted, the request witnessed.
"""
from unittest import mock

from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType, Project, ProjectStatus
from aisc_backend.models.ai_system import AISystem
from aisc_backend.routers.component import router as component_router
from aisc_backend.routers.project import router as project_router
from aisc_backend.tests.test_ledger_events import PID, Recording, events, unrecorded

A = PID
B = "0b9c7a1e-0000-4000-8000-0000000000b2"
UNLINKED = "this project is linked to no platform project, so a component cannot name a target or connection"

projects = TestAsyncClient(project_router)
components = TestAsyncClient(component_router)


def foreign(value):
    return f"{value} names another project's target or connection: a component may name only its own project's"


def malformed(value):
    return f"{value} is not a well-formed target or connection reference"


class ComponentReferences(Recording):
    def setUp(self):
        super().setUp()
        for module in ("project", "component"):
            patcher = mock.patch(f"aisc_backend.routers.{module}.log_action")
            setattr(self, f"audit_{module}", patcher.start())
            self.addCleanup(patcher.stop)

    async def _create(self, json_value, project=None):
        project = project or self.project
        return await projects.post(f"/{project.pid}/components", json={
            "name": "t", "component_type": "resource", "json_value": json_value})

    async def _refused_create(self, value, detail, project=None):
        count, before = await AIComponent.objects.acount(), await events()
        res = await self._create({"value": value}, project)
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(res.json(), {"detail": detail})
        self.assertEqual(await AIComponent.objects.acount(), count)
        self.assertEqual(await events(), before)
        self.audit_project.assert_not_called()

    async def _component(self, value):
        with unrecorded():
            return await AIComponent.objects.acreate(system=self.system, name="c", description="", data="",
                                                     component_type=AIComponentType.RESOURCE,
                                                     json_value={"value": value})

    # Creating

    async def test_a_component_naming_its_own_projects_target_is_saved(self):
        res = await self._create({"value": f"target:{A}/system"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["json_value"], {"value": f"target:{A}/system"})

    async def test_a_component_naming_its_own_projects_connection_is_saved(self):
        res = await self._create({"value": f"connection:{A}/spare"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["json_value"], {"value": f"connection:{A}/spare"})

    async def test_another_projects_connection_is_refused(self):
        await self._refused_create(f"connection:{B}/spare", foreign(f"connection:{B}/spare"))

    async def test_another_projects_target_is_refused(self):
        await self._refused_create(f"target:{B}/system", foreign(f"target:{B}/system"))

    async def test_a_reference_that_is_not_well_formed_is_refused(self):
        await self._refused_create("connection:not-a-pid/spare", malformed("connection:not-a-pid/spare"))

    async def test_a_project_linked_to_no_platform_project_names_no_reference(self):
        with unrecorded():
            unlinked = await Project.objects.acreate(name="unlinked", status=ProjectStatus.Ready,
                                                     platform_project_id=None)
            await AISystem.objects.acreate(project=unlinked, name="sys", description="")
        await self._refused_create(f"target:{A}/system", UNLINKED, project=unlinked)

    async def test_other_values_are_not_checked(self):
        res = await self._create({"value": "something-else"})
        self.assertEqual(res.status_code, 200, res.text)
        res = await self._create({"endpoint_url": "http://x"})
        self.assertEqual(res.status_code, 200, res.text)

    async def test_pids_are_compared_as_uuids(self):
        res = await self._create({"value": f"connection:{A.upper()}/spare"})
        self.assertEqual(res.status_code, 200, res.text)

    # Changing

    async def test_changing_a_value_to_another_projects_connection_is_refused(self):
        comp = await self._component(f"connection:{A}/spare")
        before = await events()
        res = await components.patch(f"/{comp.pid}", json={"json_value": {"value": f"connection:{B}/spare"}})
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(res.json(), {"detail": foreign(f"connection:{B}/spare")})
        stored = await AIComponent.objects.aget(pk=comp.pk)
        self.assertEqual(stored.json_value, {"value": f"connection:{A}/spare"})
        self.assertEqual(await events(), before)
        self.audit_component.assert_not_called()

    async def test_a_change_that_sends_no_value_checks_nothing(self):
        comp = await self._component(f"connection:{B}/spare")
        res = await components.patch(f"/{comp.pid}", json={"name": "renamed"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["name"], "renamed")

    async def test_changing_a_value_to_its_own_projects_target_is_saved(self):
        comp = await self._component(f"connection:{A}/spare")
        res = await components.patch(f"/{comp.pid}", json={"json_value": {"value": f"target:{A}/system"}})
        self.assertEqual(res.status_code, 200, res.text)
        stored = await AIComponent.objects.aget(pk=comp.pk)
        self.assertEqual(stored.json_value, {"value": f"target:{A}/system"})
