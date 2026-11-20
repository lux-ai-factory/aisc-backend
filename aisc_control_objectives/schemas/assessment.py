"""
Schemas for the assessment-coverage surface (B4).
"""
import uuid

from ninja import Schema


class CoveringChecklistOut(Schema):
    pid: uuid.UUID
    title: str
    catalogue_id: str


class CoveringPluginOut(Schema):
    name: str
    display_name: str


class ObjectiveCoverageOut(Schema):
    objective_id: str
    key: bool
    score: float
    rank: int
    mapped_risks: int
    checklists: list[CoveringChecklistOut]
    plugins: list[CoveringPluginOut]


class AssessmentCoverageOut(Schema):
    objectives: list[ObjectiveCoverageOut]
    key_objectives: int
    key_covered: int
