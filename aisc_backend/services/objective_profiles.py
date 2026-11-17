"""
The two built-in objective profiles (pilot parity).

The pilot's control-objectives app ships two profiles a project can start from
(`docs/appendices/2-control-objectives.md`): **Full AI Act** (all 50 objectives)
and **Fairness and oversight** (10). A first assessment starts on the second one,
and "Change profile" switches.

The 10 are the objectives of the fairness-and-oversight dimensions of the
catalogue: the AI Act macro requirements **REQ1 Human Agency and Oversight**,
**REQ5 Fairness** and **REQ7 Accountability** (4 + 3 + 3 = 10). The membership is
derived from the catalogue's own macro requirement rather than hard-coded, so a
changed catalogue cannot silently drift from the profile.

Nothing here is stored: a profile is a *choice*, and the objectives it picks are
what `ObjectiveProfile.objectives` holds.
"""
from __future__ import annotations

#: The objectives of these macro requirements make up "Fairness and oversight".
FAIRNESS_OVERSIGHT_REQS = ("REQ1", "REQ5", "REQ7")

FULL_AI_ACT = "full-ai-act"
FAIRNESS_OVERSIGHT = "fairness-oversight"

#: Which profile a project with no stored profile is on ("a first assessment
#: starts on the latter").
DEFAULT_PROFILE = FAIRNESS_OVERSIGHT

BUILTIN_KEYS = (FULL_AI_ACT, FAIRNESS_OVERSIGHT)

NAMES = {
    FULL_AI_ACT: "Full AI Act",
    FAIRNESS_OVERSIGHT: "Fairness and oversight",
}

DESCRIPTIONS = {
    FULL_AI_ACT: "Every objective of the AI Act catalogue (O1..O50).",
    FAIRNESS_OVERSIGHT: (
        "The objectives of the fairness and oversight dimensions: "
        "REQ1 Human Agency and Oversight, REQ5 Fairness and REQ7 Accountability."
    ),
}


def _macro_id(objective) -> str:
    """REQ1 from 'REQ1 Human Agency and Oversight' (the model property, but this
    also works on a plain dict from a test)."""
    requirement = getattr(objective, "macro_requirement", None)
    if requirement is None and isinstance(objective, dict):
        requirement = objective.get("macro_requirement", "")
    return (requirement or "").split(None, 1)[0]


def fairness_oversight_ids(objectives) -> list[str]:
    """The catalogue objectives of the fairness-and-oversight dimensions."""
    return [
        objective.objective_id
        for objective in objectives
        if _macro_id(objective) in FAIRNESS_OVERSIGHT_REQS
    ]


def full_ai_act_ids(objectives) -> list[str]:
    """Every objective of the visible catalogue."""
    return [objective.objective_id for objective in objectives]


def builtin_profiles(objectives) -> list[dict]:
    """The two built-in profiles, each with the objective ids it selects.

    `objectives` is the project's *visible* catalogue (the seeded rows plus its
    own), so a project that adds a local objective sees it in "Full AI Act".
    """
    return [
        {
            "key": FULL_AI_ACT,
            "name": NAMES[FULL_AI_ACT],
            "description": DESCRIPTIONS[FULL_AI_ACT],
            "objectives": full_ai_act_ids(objectives),
        },
        {
            "key": FAIRNESS_OVERSIGHT,
            "name": NAMES[FAIRNESS_OVERSIGHT],
            "description": DESCRIPTIONS[FAIRNESS_OVERSIGHT],
            "objectives": fairness_oversight_ids(objectives),
        },
    ]


def profile_ids(key: str, objectives) -> list[str]:
    """The objective ids one built-in profile selects (KeyError on an unknown key)."""
    if key == FULL_AI_ACT:
        return full_ai_act_ids(objectives)
    if key == FAIRNESS_OVERSIGHT:
        return fairness_oversight_ids(objectives)
    raise KeyError(key)


def default_ids(objectives) -> list[str]:
    """The selection of the profile a fresh project starts on."""
    return profile_ids(DEFAULT_PROFILE, objectives)
