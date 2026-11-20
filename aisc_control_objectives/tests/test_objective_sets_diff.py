"""Comparing two versions of a custom objective set (QUAL-01 remainder).

A set's history is only useful if a reviewer can see the change itself: which rows
appeared, which went, and — for a row whose code stayed — which fields moved.
`services.objective_sets.diff_rows` is that decision, pure: rows in, differences
out. Two properties matter beyond "it lists them": rows are matched **by code**
(a reworded row is a change, not a remove plus an add), and a change names the
fields with both values, so a reviewer reads it rather than a flag.
"""
from django.test import SimpleTestCase

from aisc_control_objectives.services.objective_sets import FIELDS, diff_rows

V1 = [
    {"objective_id": "CX-1", "macro_requirement": "House rule",
     "text": "Keep a model card", "target": "G"},
    {"objective_id": "CX-2", "macro_requirement": "House rule",
     "text": "Log every decision", "target": "G, S"},
]


class DiffRowsTest(SimpleTestCase):

    def test_a_reworded_row_is_a_change_not_a_remove_plus_an_add(self):
        after = [dict(V1[0], text="Keep a signed model card"), V1[1]]
        result = diff_rows(V1, after)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual([c["objective_id"] for c in result["changed"]], ["CX-1"])
        self.assertEqual(result["unchanged"], ["CX-2"])
        field = result["changed"][0]["fields"]
        self.assertEqual(field, [{
            "field": "text",
            "before": "Keep a model card",
            "after": "Keep a signed model card",
        }])

    def test_added_and_removed_rows_are_codes(self):
        result = diff_rows(V1, [V1[0], {"objective_id": "CX-3", "text": "New"}])
        self.assertEqual(result["added"], ["CX-3"])
        self.assertEqual(result["removed"], ["CX-2"])

    def test_a_missing_field_reads_as_empty_not_as_a_difference(self):
        """An older version may simply not carry a column: that is not an edit."""
        before = [{"objective_id": "CX-1", "text": "x"}]
        after = [{"objective_id": "CX-1", "text": "x", "notes": None}]
        result = diff_rows(before, after)
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["unchanged"], ["CX-1"])

    def test_every_catalogue_field_is_compared(self):
        """The editor can change them all, so the diff must see them all."""
        before = [{"objective_id": "CX-1", **{f: "old" for f in FIELDS}}]
        after = [{"objective_id": "CX-1", **{f: "new" for f in FIELDS}}]
        result = diff_rows(before, after)
        self.assertEqual(
            [c["field"] for c in result["changed"][0]["fields"]], list(FIELDS)
        )

    def test_publishing_nothing_butted_against_an_empty_side_reads_as_all_added(self):
        result = diff_rows([], V1)
        self.assertEqual(result["added"], ["CX-1", "CX-2"])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["changed"], [])

    def test_the_same_versions_compare_equal(self):
        result = diff_rows(V1, V1)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["unchanged"], ["CX-1", "CX-2"])
