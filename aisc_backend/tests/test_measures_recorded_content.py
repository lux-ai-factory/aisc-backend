"""The ledger event of recorded measures carries the measures themselves, frozen: one canonical row per
measure (its test, name, score, uncertainty, unit, direction and error, and the hash of its dimensions or
None, never the dimensions, which may hold the system's answers). A score that is not a finite number is written as a
string, since canonical JSON has no NaN or infinity."""
import json
import math

from aisc_backend.routers import internal
from aisc_backend.tests.run_ledger_support import RunRecording, canonical_sha256, events_of

TIME = "2026-10-18T10:00:00Z"


class MeasuresRecordedContent(RunRecording):
    async def _record(self, measures):
        await self.start()
        res = await self.as_the_worker("post", f"/evaluations/{self.evaluation.pid}/measures",
                                       json={str(self.evaluation_plugin.pid): measures})
        self.assertEqual(res.status_code, 201, res.text)
        [recorded] = await events_of("engine.measures.recorded")
        return recorded

    async def test_measures_recorded_carries_canonical_rows(self):
        recorded = await self._record([
            {"name": "F1", "score": 0.8, "time": TIME},
            {"name": "Accuracy", "score": 0.9, "uncertainty": 0.05, "unit": "ratio", "direction": "higher",
             "time": TIME, "dimensions": {"concern": "gender"}},
            {"name": "Refusals", "score": 3, "error": "two answers unreadable", "time": TIME}])
        test = str(self.evaluation_plugin.pid)
        self.assertEqual(recorded["details"], {"count": 3})
        self.assertEqual(recorded.get("content"), [
            {"evaluation_plugin": test, "name": "Accuracy", "score": 0.9, "uncertainty": 0.05, "unit": "ratio",
             "direction": "higher", "error": "N/A", "dimensions_sha256": canonical_sha256({"concern": "gender"})},
            {"evaluation_plugin": test, "name": "F1", "score": 0.8, "uncertainty": 0.0, "unit": "N/A",
             "direction": "", "error": "N/A", "dimensions_sha256": None},
            {"evaluation_plugin": test, "name": "Refusals", "score": 3.0, "uncertainty": 0.0, "unit": "N/A",
             "direction": "", "error": "two answers unreadable", "dimensions_sha256": None}])

    async def test_dimensions_are_hashed_not_copied(self):
        recorded = await self._record([{"name": "Answer", "score": 1, "time": TIME,
                                        "dimensions": {"response": "the system's own words"}}])
        self.assertTrue(recorded.get("content"), "the event carries no measures")
        self.assertNotIn("the system's own words", json.dumps(recorded))
        self.assertEqual(recorded["content"][0]["dimensions_sha256"],
                         canonical_sha256({"response": "the system's own words"}))

    def test_non_finite_scores_are_recorded_as_strings(self):
        content_of = getattr(internal, "measures_content", None)
        self.assertIsNotNone(content_of, "no measures_content in routers/internal.py")

        class Row:
            def __init__(self, name, score):
                self.name, self.score, self.uncertainty, self.unit = name, score, 0.0, "N/A"
                self.direction, self.error, self.dimensions = "", "N/A", None
                self.evaluation_plugin = "t"

        rows = content_of([Row("a", math.nan), Row("b", math.inf), Row("c", -math.inf)])
        self.assertEqual([r["score"] for r in rows], ["NaN", "Infinity", "-Infinity"])
        json.dumps(rows, allow_nan=False)

    async def test_many_measures_are_split_over_events_the_platform_takes(self):
        await self.start()
        measures = [{"name": f"Pass rate of concern number {n:04d} " + "x" * 120, "score": n / 1000, "time": TIME}
                    for n in range(400)]
        res = await self.as_the_worker("post", f"/evaluations/{self.evaluation.pid}/measures",
                                       json={str(self.evaluation_plugin.pid): measures})
        self.assertEqual(res.status_code, 201, res.text)
        recorded = await events_of("engine.measures.recorded")
        self.assertGreater(len(recorded), 1)
        self.assertEqual(sum(event["details"]["count"] for event in recorded), 400)
        self.assertEqual(sorted(row["name"] for event in recorded for row in event["content"]),
                         sorted(m["name"] for m in measures))
        for event in recorded:
            self.assertLess(len(json.dumps(event).encode()), 32 * 1024)
