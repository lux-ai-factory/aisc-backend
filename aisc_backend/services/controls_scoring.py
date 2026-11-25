"""
Readiness + coverage scoring for checklist submissions.

Port of the reference `scoring.ts`, kept as pure functions (no DB, no store):
  coverage  = answered questions / all questions, as 0-100%
  readiness = the scored questions' 0-100%: each score on the scale with
              1 "Not started" at 0% and 5 "Optimized" at 100% ((score-1)/4),
              averaged and rounded half up. Null until something is scored.
"""

from __future__ import annotations

SCORE_VALUES = (1, 2, 3, 4, 5)

SCORE_LABELS = {
    1: "Not started",
    2: "Ad-hoc",
    3: "Developing",
    4: "Established",
    5: "Optimized",
}


def parse_score(raw: str | None) -> int | None:
    """A raw form value into a 1-5 score, or None when blank/out of range."""
    if raw is None or raw.strip() == "":
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if 1 <= n <= 5 else None


def readiness_percent(scores: list[int | None]) -> int | None:
    """The scored questions' readiness as a rounded percent, null if none."""
    scored = [score for score in scores if score is not None]
    if not scored:
        return None
    average = sum((score - 1) / 4 for score in scored) / len(scored)
    return round(average * 100)


def coverage(answered: int, total: int) -> dict:
    """Coverage counts + percent (null when the checklist has no questions)."""
    percent = round((answered / total) * 100) if total > 0 else None
    return {"answered": answered, "total": total, "percent": percent}
