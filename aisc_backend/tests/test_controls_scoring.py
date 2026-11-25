from unittest import TestCase

from aisc_backend.services.controls_scoring import readiness_percent


class ReadinessPercentTest(TestCase):
    def test_ignores_unscored_answers(self):
        cases: list[tuple[list[int | None], int | None]] = [
            ([4], 75),
            ([4, None], 75),
            ([1, None, 5], 50),
            ([None], None),
            ([None, None], None),
            ([], None),
        ]
        for scores, expected in cases:
            with self.subTest(scores=scores):
                self.assertEqual(readiness_percent(scores), expected)
