"""The file a run read is the file it keeps. A component's uploaded file is recorded with its hash; a run
names, at its start, the stored object and hash of every file input it uses; and once a run has used a
component, its file is not replaced in place (a new file needs a new component), so a past run never points
at bytes it did not read."""
import hashlib
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from ninja.testing import TestAsyncClient

from aisc_backend.models import AIComponent, AIComponentType, EvaluationInput
from aisc_backend.routers.component import router as component_router
from aisc_backend.tests.run_ledger_support import RunRecording, events_of, unrecorded

components = TestAsyncClient(component_router)
ROWS = b"question,answer\nhello,hi\n"


class RunInputFilesRecorded(RunRecording):
    async def _dataset(self):
        with unrecorded():
            return await AIComponent.objects.acreate(component_type=AIComponentType.DATASET, system=self.system,
                                                     name="questions", description="", data="")

    async def _upload(self, component, body=ROWS):
        upload = SimpleUploadedFile("rows.csv", body, content_type="text/csv")
        with mock.patch("aisc_backend.routers.component.file_repository.upload_file", return_value=True):
            return await components.put(f"/{component.pid}/data", FILES={"file": upload})

    async def test_uploading_a_components_file_records_its_hash(self):
        dataset = await self._dataset()
        res = await self._upload(dataset)
        self.assertEqual(res.status_code, 200, res.text)
        [event] = await events_of("engine.component.file_uploaded")
        self.assertEqual(event["details"].get("sha256"), hashlib.sha256(ROWS).hexdigest())

    async def test_a_run_names_the_file_and_hash_of_each_input_it_uses(self):
        dataset = await self._dataset()
        res = await self._upload(dataset)
        self.assertEqual(res.status_code, 200, res.text)
        stored = res.json()["file_name"]
        with unrecorded():
            await EvaluationInput.objects.acreate(evaluation_plugin=self.evaluation_plugin, name="dataset",
                                                  component=dataset, value={})
        await self.start()
        [event] = await events_of("engine.evaluation.run_requested")
        self.assertEqual(event["details"].get("inputs"), [
            {"plugin": "DemoPlugin", "input": "dataset", "component": str(dataset.pid), "file": stored,
             "sha256": hashlib.sha256(ROWS).hexdigest()}])

    async def test_the_file_of_a_component_a_run_used_is_not_replaced_in_place(self):
        dataset = await self._dataset()
        self.assertEqual((await self._upload(dataset)).status_code, 200)
        first = (await AIComponent.objects.aget(pk=dataset.pk)).data
        with unrecorded():
            await EvaluationInput.objects.acreate(evaluation_plugin=self.evaluation_plugin, name="dataset",
                                                  component=dataset, value={})
        res = await self._upload(dataset, b"other,rows\n")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual((await AIComponent.objects.aget(pk=dataset.pk)).data, first)
