"""
Schemas for the reporting endpoints.
"""
import uuid
from typing import Any

from ninja import Schema

# the look contract: the fonts the report image ships, and the mark levels
FONT_CHOICES = (
    "inter", "liberation-sans", "liberation-serif", "roboto", "dejavu-sans",
    "dejavu-serif",
)
MARKING_CHOICES = ("none", "public", "internal", "confidential", "strictly_confidential")
LOGO_MIMES = ("image/png", "image/jpeg", "image/svg+xml")


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
    generate call does not pick its own, plus the full look contract
    (what the report's cover and pages say). Empty sections means "all"."""

    default_format: str = "pdf"  # pdf | html
    default_sections: list[str] = []
    # customisation: cover + colours
    report_title: str = ""
    subtitle: str = ""
    primary_color: str = "#000fdf"
    accent_color: str = "#ff007e"
    # the look contract: typeface, page texts, marking, document id, logo
    font: str = "inter"
    font_size_pt: float = 11
    header_text: str = ""
    footer_text: str = ""
    marking: str = "none"
    show_document_id: bool = False
    logo: dict[str, Any] | None = None


class ReportSettingsOut(Schema):
    pid: uuid.UUID
    default_format: str
    default_sections: list[str]
    report_title: str
    subtitle: str
    primary_color: str
    accent_color: str
    font: str
    font_size_pt: float
    header_text: str
    footer_text: str
    marking: str
    show_document_id: bool
    logo: dict[str, Any] | None
    updated_by: str


class ReportOut(Schema):
    pid: uuid.UUID
    project_id: uuid.UUID
    period: str
    version: int
    format: str
    file_ref: str
    status: str
    system_version: dict[str, Any] | None = None
    controls: list[dict[str, Any]] = []
    objectives: dict[str, Any] | None = None
    evaluations: list[dict[str, Any]] = []
    created_at: str
