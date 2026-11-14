"""Deleting a component is in the audit log, as creating, changing, uploading, retiring and restoring one are:
the entry names the component and its type, so the deletion can be explained afterwards."""
from unittest import mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router

client = TestAsyncClient(router)


class DeleteAuditedTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.dataset = AIComponent.objects.create(component_type=AIComponentType.DATASET, system=self.system,
                                                  name="organisations.csv", description="", data="")

    async def test_deleting_a_component_writes_an_audit_entry(self):
        with mock.patch("aisc_backend.routers.component.log_action") as log_action:
            res = await client.delete(f"/{self.dataset.pid}")
        self.assertEqual(res.status_code, 204, res.text)
        deletes = [c for c in log_action.call_args_list if c.kwargs.get("action") == "delete"]
        self.assertEqual(len(deletes), 1, f"one delete entry, got {log_action.call_args_list}")
        entry = deletes[0].kwargs
        self.assertEqual(entry["resource_type"], "component")
        self.assertEqual(entry["resource_id"], str(self.dataset.pid))
        self.assertEqual(entry["metadata"]["name"], "organisations.csv")
        self.assertEqual(entry["metadata"]["component_type"], AIComponentType.DATASET)

    async def test_a_missing_component_writes_no_audit_entry(self):
        with mock.patch("aisc_backend.routers.component.log_action") as log_action:
            res = await client.delete("/00000000-0000-0000-0000-000000000000")
        self.assertEqual(res.status_code, 404)
        log_action.assert_not_called()
