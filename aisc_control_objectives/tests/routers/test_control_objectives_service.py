"""
Tests for the control-objectives prioritising service (the math behind the
risk & control matrix), ported from the reference's priorities.

No DB, no network: pure functions with crafted inputs.
"""
from django.test import SimpleTestCase

from aisc_control_objectives.services import control_objectives as p


class BandTest(SimpleTestCase):
    def test_bands(self):
        self.assertEqual(p.band(1), "Low")
        self.assertEqual(p.band(4), "Low")
        self.assertEqual(p.band(5), "Medium")
        self.assertEqual(p.band(9), "Medium")
        self.assertEqual(p.band(10), "High")
        self.assertEqual(p.band(16), "High")
        self.assertEqual(p.band(17), "Critical")
        self.assertEqual(p.band(25), "Critical")

    def test_out_of_range(self):
        # 0 is UNRATED (treated as Low by the band table), not an error;
        # 26 exceeds any band.
        self.assertEqual(p.band(0), "Low")
        with self.assertRaises(ValueError):
            p.band(26)


class PrioritiseTest(SimpleTestCase):
    def _objectives(self, ids):
        return [
            {"id": oid, "text": f"objective {oid}", "non_binding": False, "sort_key": oid}
            for oid in ids
        ]

    def test_score_is_sum_of_mapped_risk_ratings(self):
        objectives = self._objectives(["O1", "O2", "O3"])
        # O1 mitigates r-a (rating 4*5=20) and r-b (2*2=4) -> 24
        # O2 mitigates only r-a                       -> 20
        # O3 maps to nothing                          -> 0
        mappings = {"O1": ["r-a", "r-b"], "O2": ["r-a"]}
        rating = {"r-a": 20, "r-b": 4}

        priorities = p.prioritise(
            objectives=objectives, mappings=mappings, rating_of=lambda rid: rating[rid],
        )
        by_id = {pr.objective_id: pr for pr in priorities}
        self.assertEqual(by_id["O1"].score, 24.0)
        self.assertEqual(by_id["O2"].score, 20.0)
        self.assertEqual(by_id["O3"].score, 0.0)
        self.assertEqual(by_id["O1"].top_rating, 20)
        self.assertEqual(by_id["O3"].top_rating, 0)

    def test_rank_orders_by_score_then_top_rating(self):
        objectives = self._objectives(["O1", "O2", "O3"])
        mappings = {"O1": ["r1"], "O2": ["r2"], "O3": ["r3"]}
        rating = {"r1": 22, "r2": 4, "r3": 10}
        priorities = p.prioritise(
            objectives=objectives, mappings=mappings, rating_of=lambda rid: rating[rid],
        )
        # ranks follow the score: O1=22, O3=10, O2=4
        ordered = sorted(priorities, key=lambda pr: pr.rank)
        self.assertEqual([pr.objective_id for pr in ordered], ["O1", "O3", "O2"])
        self.assertEqual([pr.rank for pr in ordered], [1, 2, 3])

    def test_key_objectives_by_default_from_high_risk(self):
        objectives = self._objectives([f"O{i}" for i in range(1, 12)])
        mappings = {f"O{i}": [f"r{i}"] for i in range(1, 12)}
        # each rated above the KEY_RATING (10) -> all qualify, but budget is 7
        rating = {f"r{i}": 11 + (i % 3) for i in range(1, 12)}
        priorities = p.prioritise(
            objectives=objectives, mappings=mappings, rating_of=lambda rid: rating[rid],
            budget=7,
        )
        keyed = [pr.objective_id for pr in priorities if pr.key]
        self.assertEqual(len(keyed), 7)

    def test_voluntary_ranks_last_and_never_key_by_default(self):
        objectives = self._objectives(["O1", "O2"])
        objectives.append(
            {"id": "O3", "text": "t", "non_binding": True, "sort_key": "O3"}
        )
        mappings = {"O1": ["r1"], "O2": ["r2"], "O3": ["r3"]}
        rating = {"r1": 22, "r2": 18, "r3": 25}
        priorities = p.prioritise(
            objectives=objectives, mappings=mappings, rating_of=lambda rid: rating[rid],
        )
        # O3 has the highest score but is voluntary -> last, not key
        self.assertEqual(
            [pr.objective_id for pr in priorities][-1], "O3"
        )
        self.assertFalse([pr for pr in priorities if pr.objective_id == "O3"][0].key)
        self.assertFalse([pr for pr in priorities if pr.objective_id == "O3"][0].key_default)

    def test_assessor_key_choices_override_default(self):
        objectives = self._objectives(["O1", "O2", "O3"])
        mappings = {"O1": ["r1"], "O2": ["r2"], "O3": []}
        rating = {"r1": 22, "r2": 4}
        priorities = p.prioritise(
            objectives=objectives, mappings=mappings, rating_of=lambda rid: rating[rid],
            keys={"O3": True},
        )
        by_id = {pr.objective_id: pr for pr in priorities}
        self.assertTrue(by_id["O3"].key)          # assessor forced it on
        self.assertFalse(by_id["O3"].key_default) # but it is not a default
