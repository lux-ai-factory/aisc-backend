"""A component's uploaded file has a size cap, is never empty and carries an extension of its component's type.

Every refusal happens before anything is stored."""
import os
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType
from aisc_backend.models.ai_system import AISystem
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.component import router


client = TestAsyncClient(router)
STORE = "aisc_backend.routers.component.file_repository.upload_file"


class ComponentUploadLimitsTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="proj", status=ProjectStatus.Ready)
        self.system = AISystem.objects.create(project=self.project, name="sys", description="")

    async def _component(self, component_type):
        return await AIComponent.objects.acreate(
            component_type=component_type, system=self.system, name=component_type, description="", data="")

    async def _put(self, comp, name, body):
        upload = SimpleUploadedFile(name or "placeholder", body)
        if not name:
            upload._name = ""  # a browser can send a part with no file name; Django refuses to build one
        with patch(STORE, return_value=True) as stored:
            res = await client.put(f"/{comp.pid}/data", FILES={"file": upload})
        return res, stored

    async def test_an_empty_file_is_refused(self):
        comp = await self._component(AIComponentType.DATASET)
        res, stored = await self._put(comp, "rows.csv", b"")
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn("empty", res.json()["detail"])
        stored.assert_not_called()

    async def test_a_file_above_the_cap_is_refused_and_never_stored(self):
        comp = await self._component(AIComponentType.DATASET)
        with patch.dict(os.environ, {"TARGET_FILE_MAX_MB": "1"}):
            res, stored = await self._put(comp, "rows.csv", b"x" * (1024 * 1024 + 1))
        self.assertEqual(res.status_code, 413, res.text)
        self.assertIn("1 MB", res.json()["detail"])
        stored.assert_not_called()

    async def test_a_dataset_takes_only_data_extensions_and_a_model_only_model_ones(self):
        data = await self._component(AIComponentType.DATASET)
        model = await self._component(AIComponentType.MODEL)
        for comp, name in ((data, "model.onnx"), (data, "x.exe"), (model, "rows.csv"), (model, "noext")):
            res, stored = await self._put(comp, name, b"abc")
            self.assertEqual(res.status_code, 415, (name, res.text))
            self.assertIn(".csv" if comp is data else ".onnx", res.json()["detail"])
            stored.assert_not_called()

    async def test_a_file_of_the_right_type_is_still_accepted(self):
        comp = await self._component(AIComponentType.DATASET)
        res, stored = await self._put(comp, "rows.csv", b"a,b\n1,2\n")
        self.assertEqual(res.status_code, 200, res.text)
        stored.assert_called_once()

    async def test_a_file_without_a_name_is_422_not_500(self):
        comp = await self._component(AIComponentType.DATASET)
        res, stored = await self._put(comp, "", b"abc")
        self.assertEqual(res.status_code, 422, res.text)
        stored.assert_not_called()
