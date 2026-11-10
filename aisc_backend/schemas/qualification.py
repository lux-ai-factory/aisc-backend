"""
Schemas for qualification + the system-versions (AI card versioning) contract.

Shapes mirror the reference app's PlatformClient types so the headless service
(discarded, but its contract lives on) and the future webapp speak the same
JSON. A SystemVersion card shape:
  pid, project_id, number, name, version, provider, description,
  created_at, created_by.

The qualification payload is deliberately thin: `answers` (the form the service
defines) in, `system_card` (the card the service built) out, plus the card
version it produced. Form fields are not mirrored as columns — they would
couple the single DB to the service's questionnaire schema.
"""
import uuid
from typing import Any

from ninja import Schema
from pydantic import ConfigDict


class SystemVersionOut(Schema):
    pid: uuid.UUID
    project_id: uuid.UUID
    number: int
    name: str
    version: str
    provider: str
    description: str
    created_at: str
    created_by: str


class SystemVersionIn(Schema):
    name: str
    version: str | None = None
    provider: str | None = None
    description: str | None = None


class QualificationIn(Schema):
    """What a save sends: the questionnaire it was answered against and the
    form's answers. The card name (a display label) is optional; the backend
    falls back to the project name."""

    questionnaire_id: str = ""
    name: str = ""
    answers: dict[str, Any] = {}
    #: the questionnaire version the answers were filled against (latest when omitted)
    questionnaire_version: int | None = None


class QualificationOut(Schema):
    pid: uuid.UUID
    questionnaire_id: str
    questionnaire_version: int | None = None
    answers: dict[str, Any]
    system_card: dict[str, Any] | None = None
    status: str


class QualificationDetailOut(QualificationOut):
    system_version: SystemVersionOut | None = None
    system_card_jsonld: str | None = None


class QuestionnaireOut(Schema):
    pid: uuid.UUID
    name: str
    definition: dict[str, Any]
    #: latest config revision (1..n), 0 when none recorded yet
    version: int = 0


class QuestionnaireIn(Schema):
    name: str
    definition: dict[str, Any] = {}


class QuestionnaireSelectionOut(Schema):
    questionnaire_id: str | None


class DefaultQuestionnaireOut(Schema):
    """The builtin default, owned by the headless service (read-only here):
    no versions, no edits — user copies live in the backend."""

    id: str
    name: str
    description: str
    version: int
    definition: dict[str, Any]


class QuestionnaireVersionOut(Schema):
    """One saved revision of a questionnaire (B1): the form as it was."""

    pid: uuid.UUID
    questionnaire_id: uuid.UUID
    number: int
    definition: dict[str, Any]
    created_at: str
    created_by: str


class QualificationCardIn(Schema):
    """An edited AI card (B3): stored as a new qualification row, never in place."""

    system_card: dict[str, Any]


class NodePatchIn(Schema):
    """One reviewer's correction to one card node, applied by the ontology on
    rebuild: a new label, a VAIR term (`null` removes the type), a note, or the
    mark that no vocabulary term applies. Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    vair: str | None = None
    note: str | None = None
    termNotApplicable: bool | None = None


class NodesPatchIn(Schema):
    """A card's staged corrections, saved together as one version: node id to
    its correction (the same keys as NodePatchIn). An empty correction clears
    that node's correction."""

    nodes: dict[str, NodePatchIn]
