"""Deleting a component that no evaluation used removes its uploaded file from the bucket too, once the row is
gone: a file named by no row is unreachable and would stay in the shared bucket for ever. A file the store
refuses to remove does not undo the delete."""
from unittest import mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router

client = TestAsyncClient(router)


class DeleteRemovesTheFileTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.dataset = AIComponent.objects.create(
            component_type=AIComponentType.DATASET, system=self.system, name="organisations.csv", description="",
            data="5f0c1d2e-0000-4000-8000-000000000001.csv", storage_container=StorageContainer.Datasets)
        self.store = mock.MagicMock()

    def _removed(self):
        """(bucket, key) of every object removal the engine asked of the store."""
        out = [(c.kwargs.get("Bucket"), c.kwargs.get("Key")) for c in self.store.delete_object.call_args_list]
        for c in self.store.delete_objects.call_args_list:
            out += [(c.kwargs.get("Bucket"), o.get("Key")) for o in c.kwargs.get("Delete", {}).get("Objects", [])]
        return out

    async def test_deleting_an_unused_component_removes_its_file_from_the_bucket(self):
        with mock.patch("aisc_backend.repositories.file_repository._get_s3_client", return_value=self.store), \
                mock.patch("aisc_backend.routers.component.log_action"):
            res = await client.delete(f"/{self.dataset.pid}")
        self.assertEqual(res.status_code, 204, res.text)
        self.assertFalse(await AIComponent.objects.filter(pk=self.dataset.pk).aexists())
        self.assertEqual(self._removed(), [(StorageContainer.Datasets, self.dataset.data)],
                         "the component's file stays in the bucket, named by no row")

    async def test_a_file_the_store_refuses_to_remove_is_named_in_the_log_and_the_delete_stands(self):
        self.store.delete_object.side_effect = RuntimeError("store down")
        self.store.delete_objects.side_effect = RuntimeError("store down")
        with mock.patch("aisc_backend.repositories.file_repository._get_s3_client", return_value=self.store), \
                mock.patch("aisc_backend.routers.component.log_action"), \
                self.assertLogs(level="WARNING") as logged:
            res = await client.delete(f"/{self.dataset.pid}")
        self.assertEqual(res.status_code, 204, res.text)
        self.assertFalse(await AIComponent.objects.filter(pk=self.dataset.pk).aexists())
        self.assertTrue(any(self.dataset.data in line for line in logged.output),
                        f"the file left behind is not named in the log: {logged.output}")
