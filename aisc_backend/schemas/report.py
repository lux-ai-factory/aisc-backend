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
    "2026-Q3"), the output format, the evaluations and sections to include, the
    layout to render and the AI-card version to report on. Empty evaluations
    means "whole project"; empty sections falls back to the project's report
    settings, then to all; no layout falls back to the assessment-report
    built-in; no version means the latest."""

    period: str = ""
    format: str = "pdf"          # pdf | docx | html
    evaluation_pids: list[str] = []
    sections: list[str] = []
    layout_id: str = ""          # a project layout pid or "builtin-<slug>"
    version_pid: str = ""        # the AI-card version to report on ("" = latest)
    other_versions: bool = False
    compare_to: str = ""         # an earlier version pid to compare with


class ReportSettingsIn(Schema):
    """A project's report defaults (B5): format + sections used when a
    generate call does not pick its own, plus the full look contract
    (what the report's cover and pages say). Empty sections means "all"."""

    default_format: str = "pdf"  # pdf | docx | html
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
    layout_id: str | None = None
    layout_revision: int | None = None
    controls: list[dict[str, Any]] = []
    objectives: dict[str, Any] | None = None
    snapshot_checksum: str = ""
    document_checksum: str = ""
    evaluations: list[dict[str, Any]] = []
    created_at: str


class ReportLayoutIn(Schema):
    """Create/update a project layout: its ordered blocks and its index and
    numbering flags."""

    name: str
    description: str = ""
    blocks: list[dict[str, Any]] = []
    show_index: bool = True
    numbering: bool = True


class ReportLayoutOut(Schema):
    id: str                     # the layout pid, or "builtin-<slug>" for a built-in
    name: str
    description: str = ""
    blocks: list[dict[str, Any]] = []
    show_index: bool = True
    numbering: bool = True
    revision: int = 0
    built_in: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class LayoutDuplicateIn(Schema):
    name: str = ""


class LayoutImportIn(Schema):
    """A layout file (the pilot's export/import between projects)."""

    name: str = ""
    description: str = ""
    blocks: list[dict[str, Any]] = []
    show_index: bool = True
    numbering: bool = True


class LayoutPreviewIn(Schema):
    """Preview a layout against a version (never saved)."""

    version_pid: str = ""
    period: str = ""
    blocks: list[dict[str, Any]] | None = None

