"""
Schemas for the versioned evidence map (EVD-01).

The shape matches the pilot's evidence-map page: a selected AI-card version,
the objectives (with their risks and computed evidence status), the test and
checklist tiles, the saved links, coverage, the review, and the role-appropriate
edit/review permissions.
"""
import uuid

from ninja import Schema


class EvidenceVersionOut(Schema):
    pid: uuid.UUID
    number: int


class EvidenceRiskOut(Schema):
    id: str
    label: str
    impact: int | None = None
    likelihood: int | None = None


class EvidenceObjectiveOut(Schema):
    id: str
    title: str
    dimension: str = ""
    expects: str = ""
    priority: bool = False
    stale: str | None = None
    linked: bool = False
    risks: list[EvidenceRiskOut] = []
    #: "not covered" | "no evidence" | "evidence" | "attention"
    evidence: str = "not covered"


class EvidenceRunsOut(Schema):
    executed: int = 0
    failed: int = 0
    running: int = 0
    latest_failed: bool = False


class EvidenceSubmissionOut(Schema):
    answered: int = 0
    questions: int = 0
    readiness: int | None = None


class EvidenceTestOut(Schema):
    key: str
    label: str
    dimensions: list[str] = []
    stale: str | None = None
    runs: EvidenceRunsOut = EvidenceRunsOut()


class EvidenceControlOut(Schema):
    key: str
    label: str
    dimensions: list[str] = []
    stale: str | None = None
    submission: EvidenceSubmissionOut | None = None


class EvidenceLinkOut(Schema):
    objective_id: str
    kind: str
    key: str
    note: str = ""
    created_at: str
    created_by: str = ""


class EvidenceCoverageOut(Schema):
    linked: int = 0
    objectives: int = 0
    gaps: int = 0


class EvidenceReviewOut(Schema):
    reviewed_by: str = ""
    reviewed_by_name: str = ""
    reviewed_at: str


class EvidenceMapOut(Schema):
    version: EvidenceVersionOut | None = None
    versions: list[EvidenceVersionOut] = []
    objectives: list[EvidenceObjectiveOut] = []
    tests: list[EvidenceTestOut] = []
    controls: list[EvidenceControlOut] = []
    links: list[EvidenceLinkOut] = []
    coverage: EvidenceCoverageOut = EvidenceCoverageOut()
    review: EvidenceReviewOut | None = None
    can_edit: bool = False
    can_review: bool = False
    read_only: bool = False
    carried_from: int | None = None


class EvidenceLinkIn(Schema):
    objective_id: str
    kind: str
    key: str
    note: str | None = None


class EvidenceLinksIn(Schema):
    version: uuid.UUID
    links: list[EvidenceLinkIn]


class EvidenceReviewIn(Schema):
    version: uuid.UUID
