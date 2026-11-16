"""
Which control objectives matter most: the scores behind the risk & control
matrix. Port of the reference `prioritising.py` (kept pure and in one place).

What orders the objectives is the system's own risks, rated by the assessor:

    each risk: impact (1-5) x likelihood (1-5) = its rating, 1 to 25
    the matrix says which objectives mitigate it
    an objective scores the sum of the ratings of the risks it mitigates
    key objectives = by default the first seven driven by a High or Critical
                     risk; the assessor turns key on/off for any objective

Scores and default keys are never stored: a changed rating can never leave a
stale score, which is why this is computed on every read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: What a risk counts before both its impact and likelihood are rated.
UNRATED = 0
#: How many objectives are key by default.
KEY_BUDGET = 7
#: The lowest rating that makes an objective key by default (High).
KEY_RATING = 10
#: The 5x5 bands (ISO 31000, COSO ERM): highest rating in each, and its name.
BANDS = ((4, "Low"), (9, "Medium"), (16, "High"), (25, "Critical"))


def band(rating: int) -> str:
    """Low 1-4, Medium 5-9, High 10-16, Critical 17-25."""
    for top, name in BANDS:
        if rating <= top:
            return name
    raise ValueError(f"a rating is 1 to 25, not {rating}")


@dataclass
class Priority:
    """One objective's standing in the matrix."""

    objective_id: str
    non_binding: bool = False
    #: The sum of the ratings of the risks it mitigates.
    score: float = 0.0
    #: The highest single rating among them; 0 when none.
    top_rating: int = 0
    #: 1 .. n: score, then top rating, then a binding duty first, then catalogue order.
    rank: int = 0
    #: Key: the assessor's choice when they made one, else `key_default`.
    key: bool = False
    key_default: bool = False
    #: The risks this objective mitigates, highest rated first.
    risk_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _score(ratings: list[int], non_binding: bool) -> tuple[float, list[str]]:
    """Score + the reasons that placed this objective."""
    if ratings:
        score = float(sum(ratings))
        rated_as = ", ".join(f"{r} ({band(r)})" if r else "not rated" for r in ratings)
        reasons = [f"mitigates {len(ratings)} risk(s) rated {rated_as}: score {int(score)}"]
    else:
        score = 0.0
        reasons = ["no identified risk maps to it: owed, but not where this system's danger is"]
    if non_binding:
        reasons.append("voluntary: binds nobody, so it ranks last")
    return score, reasons


def prioritise(*, objectives, mappings, rating_of, budget: int = KEY_BUDGET,
               keys: dict[str, bool] | None = None) -> list[Priority]:
    """One Priority per objective, in catalogue order.

    `objectives`: iterable of `{id, non_binding, sort_key}`.
    `mappings`: objective_id -> list of risk_ids (already resolved).
    `rating_of`: (risk_id) -> rating (0 = unrated).
    `keys`: the assessor's key choices, objective_id -> True/False.
    """
    keyed: list[tuple[tuple, Priority]] = []
    priorities: dict[str, Priority] = {}
    for objective in objectives:
        oid = objective["id"]
        non_binding = objective["non_binding"]
        priority = Priority(objective_id=oid, non_binding=non_binding)
        priorities[oid] = priority
        risk_ids = mappings.get(oid, [])
        priority.risk_ids = list(risk_ids)
        ratings = [rating_of(rid) for rid in risk_ids]
        priority.top_rating = max(ratings) if ratings else 0
        priority.score, priority.reasons = _score(ratings, non_binding)
        binding = not non_binding
        keyed.append(((non_binding, -priority.score, -priority.top_rating,
                       not binding, objective["sort_key"]), priority))

    keyed.sort(key=lambda row: row[0])
    chosen = dict(keys or {})
    defaults = 0
    for rank, (_, priority) in enumerate(keyed, 1):
        priority.rank = rank
        if (not priority.non_binding and priority.score > 0
                and priority.top_rating >= KEY_RATING and defaults < budget):
            priority.key_default = True
            defaults += 1
        priority.key = chosen.get(priority.objective_id, priority.key_default)
    return [priorities[objective["id"]] for objective in objectives]
