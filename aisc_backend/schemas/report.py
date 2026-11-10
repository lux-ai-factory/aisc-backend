"""
Schemas for the reporting endpoints.
"""
import uuid
from typing import Any

from ninja import Schema


class ReportIn(Schema):
    """What a generate call sends: the reporting period (a free label, e.g.
    "2026-Q3"), the output format, and — since B5 — the evaluations and
    sections to include. Empty evaluations means "whole project"; empty
    sections falls back to the project's report settings, then to all."""

    period: str = ""
    format: str = "pdf"          # pdf | html
    evaluation_pids: list[str] = []
    sections: list[str] = []


class ReportSettingsIn(Schema):
    """A project's report defaults (B5): format + sections used when a
    generate call does not pick its own. Empty sections means "all"."""

    default_format: str = "pdf"  # pdf | html
    default_sections: list[str] = []


class ReportSettingsOut(Schema):
    pid: uuid.UUID
    default_format: str
    default_sections: list[str]
    updated_by: str


class ReportOut(Schema):
    pid: uuid.UUID
    project_id: uuid.UUID
    period: str
    format: str
    file_ref: str
    status: str
    system_version: dict[str, Any] | None = None
    controls: list[dict[str, Any]] = []
    objectives: dict[str, Any] | None = None
    created_at: str
