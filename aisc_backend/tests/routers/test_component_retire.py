"""Deleting a component that an evaluation used retires it instead: the evaluation keeps its inputs (the record
of what it ran on), and the component leaves the lists and the choices for new runs until it is restored."""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import (AIComponent, AIComponentType, Evaluation, EvaluationInput, EvaluationPlugin,
                                 EvaluationStatus)
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router

client = TestAsyncClient(router)


class RetireTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")
        self.dataset = AIComponent.objects.create(component_type=AIComponentType.DATASET, system=self.system,
                                                  name="data.csv", description="", data="")

    def _used_by(self, n):
        for i in range(n):
            evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=self.project)
            run = EvaluationPlugin.objects.create(evaluation=evaluation, name=f"run {i}")
            EvaluationInput.objects.create(evaluation_plugin=run, name="dataset", component=self.dataset)

    async def test_a_used_component_is_retired_not_deleted(self):
        await sync(self._used_by)(2)
        res = await client.delete(f"/{self.dataset.pid}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"retired": True, "evaluations": 2})
        kept = await AIComponent.objects.aget(pk=self.dataset.pk)
        self.assertIsNotNone(kept.retired_at)
        self.assertEqual(await EvaluationInput.objects.filter(component=kept).acount(), 2)

    async def test_an_unused_component_is_still_deleted(self):
        res = await client.delete(f"/{self.dataset.pid}")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(await AIComponent.objects.filter(pk=self.dataset.pk).aexists())

    async def test_the_component_says_whether_it_is_retired(self):
        res = await client.get(f"/{self.dataset.pid}")
        self.assertIsNone(res.json()["retired_at"])
        await sync(self._used_by)(1)
        await client.delete(f"/{self.dataset.pid}")
        res = await client.get(f"/{self.dataset.pid}")
        self.assertIsNotNone(res.json()["retired_at"])

    async def test_a_retired_component_is_restored(self):
        await sync(self._used_by)(1)
        await client.delete(f"/{self.dataset.pid}")
        res = await client.post(f"/{self.dataset.pid}/restore")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(res.json()["retired_at"])
        self.assertIsNone((await AIComponent.objects.aget(pk=self.dataset.pk)).retired_at)

    async def test_restoring_a_missing_component_is_404(self):
        res = await client.post("/00000000-0000-0000-0000-000000000000/restore")
        self.assertEqual(res.status_code, 404)


def sync(fn):
    from asgiref.sync import sync_to_async
    return sync_to_async(fn)
