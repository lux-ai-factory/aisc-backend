from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType
from aisc_backend.models.ai_system import AISystem
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.routers.project_config import router

client = TestAsyncClient(router)
project_repository = ProjectRepository()


class DeriveFeaturesRouterTestCase(TestCase):
    async def get_dataset(self, project, data="ds.csv"):
        system = await AISystem.objects.acreate(project=project, name="sys", description="")
        return await AIComponent.objects.acreate(
            name="ds", description="", data=data,
            component_type=AIComponentType.DATASET, system=system,
        )

    async def test_derive_features_requires_dataset_component(self):
        project = await project_repository.create("p")
        model = await AIComponent.objects.acreate(
            name="m", description="", component_type=AIComponentType.MODEL,
            system=await AISystem.objects.acreate(project=project, name="sys", description=""),
        )
        res = await client.post(
            f"/{project.pid}/derive-features?source_dataset_pid={str(model.pid)}",
        )
        self.assertEqual(res.status_code, 400, res.text)

    async def test_derive_features_rejects_missing_dataset(self):
        project = await project_repository.create("p")
        res = await client.post(
            f"/{project.pid}/derive-features?source_dataset_pid=00000000-0000-0000-0000-000000000000",
        )
        self.assertEqual(res.status_code, 400, res.text)

    async def test_derive_features_returns_extracted_features(self):
        project = await project_repository.create("p")
        dataset = await self.get_dataset(project)

        with patch("aisc_backend.routers.project_config.ai_component_repository.get") as mock_get, \
             patch("aisc_backend.routers.project_config.derive_datashape") as mock_derive:
            mock_get.return_value = dataset
            mock_derive.return_value = {
                "version": 1,
                "source_dataset_pid": str(dataset.pid),
                "source_format": "csv",
                "row_count": 2,
                "features": [
                    {"name": "a", "dtype": "int64", "semantic_type": "numeric", "role": "feature"},
                    {"name": "b", "dtype": "object", "semantic_type": "categorical", "role": "feature"},
                ],
            }
            res = await client.post(
                f"/{project.pid}/derive-features?source_dataset_pid={str(dataset.pid)}",
            )
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.data["source_format"], "csv")
            self.assertEqual(len(res.data["features"]), 2)
