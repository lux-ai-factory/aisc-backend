"""
The one table that ties our objectives to the catalogue's dimensions (CAT-01).

A dimension is the catalogue's own vocabulary — its items are tagged in a
`dimension` section — and ours is the objectives': each of the eleven AI Act
macro requirements REQ1..REQ11 is one. The two vocabularies do not agree word
for word ("Fairness" against "Diversity, Non-discrimination and Fairness"), so
the correspondence is a table, and this is the table's test: the slugs are the
catalogue's, every seeded requirement has a dimension, and nothing invents a
twelfth.
"""
import csv
from pathlib import Path

from django.test import SimpleTestCase

from aisc_backend.services import dimensions

CSV = Path(__file__).resolve().parent.parent / "data" / "ai_act_control_objectives.csv"

#: The catalogue's own eleven, copied from its taxonomy (its site's sidebar) and
#: confirmed against its live tags. A slug here that the catalogue does not tag
#: would filter the listing to nothing, silently.
CATALOGUE_SLUGS = [
    "human-agency-oversight",
    "technical-robustness-safety",
    "privacy-data-governance",
    "transparency",
    "diversity-non-discrimination-fairness",
    "societal-environmental-wellbeing",
    "accountability",
    "quality-management",
    "risk-management",
    "technical-documentation",
    "record-keeping",
]


class DimensionMapTest(SimpleTestCase):

    def test_every_dimension_is_one_the_catalogue_tags(self):
        self.assertEqual(list(dimensions.BY_MACRO.values()), CATALOGUE_SLUGS)

    def test_every_dimension_has_the_catalogue_s_label(self):
        self.assertEqual(sorted(dimensions.LABELS), sorted(CATALOGUE_SLUGS))

    def test_the_seeded_objectives_all_have_a_dimension(self):
        """A macro requirement in the seed with no dimension would leave its
        objectives out of every filtered listing."""
        with CSV.open(encoding="utf-8") as handle:
            requirements = {
                row["Macro_Requirement"] for row in csv.DictReader(handle)
            }
        missing = sorted(r for r in requirements if not dimensions.dimension_of(r))
        self.assertEqual(missing, [], "seeded requirements with no dimension")

    def test_a_requirement_maps_to_its_slug(self):
        self.assertEqual(
            dimensions.dimension_of("REQ9 Risk Management"), "risk-management"
        )
        self.assertEqual(
            dimensions.dimension_of("REQ10 Technical Documentation"),
            "technical-documentation",
        )
        # our own titles differ from the catalogue's, and the catalogue's win
        self.assertEqual(
            dimensions.dimension_of("REQ5 Fairness"),
            "diversity-non-discrimination-fairness",
        )
        self.assertEqual(
            dimensions.dimension_of("REQ11 Record-keeping and Documentation Retention"),
            "record-keeping",
        )

    def test_an_objective_of_the_project_s_own_has_no_dimension(self):
        """A local objective (a `CX` code) carries no dimension; nothing guesses."""
        self.assertEqual(dimensions.dimension_of("CX1 Our own objective"), "")
        self.assertEqual(dimensions.dimension_of(""), "")

    def test_dimensions_are_distinct_and_in_requirement_order(self):
        self.assertEqual(
            dimensions.dimensions_of([
                "REQ7 Accountability",
                "REQ2 Technical Robustness and Safety",
                "REQ2 Technical Robustness and Safety",
                "CX1 Our own objective",
            ]),
            ["technical-robustness-safety", "accountability"],
        )

    def test_the_vocabulary_carries_the_requirement_the_slug_and_the_label(self):
        vocabulary = dimensions.vocabulary()
        self.assertEqual(len(vocabulary), 11)
        self.assertEqual(
            vocabulary[0],
            {
                "requirement": "REQ1",
                "slug": "human-agency-oversight",
                "label": "Human Agency and Oversight",
            },
        )
        self.assertEqual([d["requirement"] for d in vocabulary][-1], "REQ11")
