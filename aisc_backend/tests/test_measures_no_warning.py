"""Recording a run's measures warns nothing: the route builds each measurement from the plugin's values and the
observation and metric it made, without putting model objects into the schema's integer fields, so the log does
not carry a serializer warning for every measure of every run."""
import warnings

from aisc_backend.tests.run_ledger_support import RunRecording

TIME = "2026-10-18T10:00:00Z"


class MeasuresWarnNothing(RunRecording):
    async def test_recording_measures_raises_no_serializer_warning(self):
        await self.start()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            res = await self.as_the_worker("post", f"/evaluations/{self.evaluation.pid}/measures",
                                           json={str(self.evaluation_plugin.pid): [
                                               {"name": "F1", "score": 0.8, "time": TIME},
                                               {"name": "Accuracy", "score": 0.9, "time": TIME,
                                                "dimensions": {"concern": "gender"}}]})
        self.assertEqual(res.status_code, 201, res.text)
        said = [str(w.message).splitlines()[0] for w in caught if "serializ" in str(w.message).lower()]
        self.assertEqual(said, [], "recording measures warns at every measure")
