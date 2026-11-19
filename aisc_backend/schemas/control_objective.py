"""
Schemas for control objectives (the risk & control matrix).
"""
import uuid

from ninja import Schema


class ControlObjectiveOut(Schema):
    id: str            # O1 .. O50
    macro_requirement: str
    legal_basis: str
    sub_requirement_label: str
    text: str
    assessment_mode: str
    standards_grounding: str
    grounding_tier_flag: str
    custom: bool = False


class ControlObjectiveIn(Schema):
    """A project's own catalogue row: same shape as the seeded catalogue."""

    objective_id: str
    macro_requirement: str
    legal_basis: str = ""
    sub_requirement_label: str = ""
    text: str = ""
    assessment_mode: str = ""
    target: str = ""
    standards_grounding: str = ""
    grounding_tier_flag: str = ""
    notes: str = ""


class RiskOut(Schema):
    pid: uuid.UUID
    position: int
    name: str
    airo_text: str
    impact: int | None = None
    likelihood: int | None = None
    rating: int = 0
    comment: str = ""
    # the risk's current revision number (0 until first rated)
    version: int = 0


class RiskRatingIn(Schema):
    impact: int | None = None
    likelihood: int | None = None
    comment: str = ""


class RiskVersionOut(Schema):
    """One saved revision of a risk's assessment (append-only, numbered)."""

    pid: uuid.UUID
    risk_pid: uuid.UUID
    number: int
    name: str
    impact: int | None = None
    likelihood: int | None = None
    rating: int = 0
    comment: str = ""
    airo_text: str = ""
    created_at: str
    created_by: str = ""


class RiskObjectiveMappingIn(Schema):
    objective_id: str
    quote: str = ""
    rationale: str = ""


class RiskObjectiveMappingOut(Schema):
    pid: uuid.UUID
    risk_pid: uuid.UUID
    objective_id: str
    quote: str
    rationale: str
    source: str


class ObjectivePriorityOut(Schema):
    objective_id: str
    score: float
    top_rating: int
    rank: int
    key: bool
    key_default: bool
    risk_ids: list[str]
    reasons: list[str]
    #: The assessor's manual per-objective likelihood (1-5), when set.
    likelihood: float | None = None
    #: The assessor's free-text note for the objective.
    comment: str = ""


class ObjectiveRatingIn(Schema):
    """The assessor's manual score/likelihood/comment for one objective."""

    score: float
    likelihood: int | None = None
    comment: str = ""


class ObjectiveRatingsIn(Schema):
    """Suite of manual score/likelihood overrides, keyed by objective_id."""

    ratings: dict[str, ObjectiveRatingIn]


class MatrixOut(Schema):
    priorities: list[ObjectivePriorityOut]
    risks: list[RiskOut]
    mappings: list[RiskObjectiveMappingOut]


class ObjectiveProfileIn(Schema):
    name: str = ""
    objectives: list[str] = []
    key_objectives: list[str] = []
    objective_ratings: dict = {}


class ObjectiveProfileOut(Schema):
    pid: uuid.UUID
    name: str
    objectives: list[str]
    key_objectives: list[str]
    catalogue_digest: str = ""
    objective_ratings: dict = {}
