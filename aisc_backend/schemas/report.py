"""
Schemas for the reporting endpoints.
"""
import uuid
from typing import Any

from ninja import Schema, Field
from pydantic import field_validator, model_validator

# the look contract: the fonts the report image ships, and the mark levels
FONT_CHOICES = (
    "inter", "liberation-sans", "liberation-serif", "roboto", "dejavu-sans",
    "dejavu-serif",
)
MARKING_CHOICES = ("none", "public", "internal", "confidential", "strictly_confidential")
LOGO_MIMES = ("image/png", "image/jpeg", "image/svg+xml")

REPORT_FORMATS = ("pdf", "html")
#: length caps the generation request shares with the prepare call (validated
#: before any report row is created)
PERIOD_LIMIT = 255
TITLE_LIMIT = 200
TEXT_LIMIT = 2000

EVALUATION_CHART_TYPES = ("line", "scatter", "kde", "bars", "radar", "pie")


class EvaluationChartPanel(Schema):
    """A browser-rendered evaluation panel: a report-owned artifact UUID (the
    PNG was uploaded as multipart) plus an optional label. Image bytes are not
    accepted in generation requests — they arrive as separate artifact uploads."""

    model_config = {"extra": "forbid"}

    artifact_id: uuid.UUID
    label: str = Field("", max_length=200)


class EvaluationChartExport(Schema):
    model_config = {"extra": "forbid"}

    evaluation_pid: uuid.UUID
    evaluation_plugin_pid: uuid.UUID
    visualization_index: int = Field(0, ge=0)
    # an empty panel list is allowed: the renderer shows an unavailable message
    panels: list[EvaluationChartPanel] = []


class DashboardWidgetRef(Schema):
    """A dashboard widget collected into the report: a chart carries its
    report-owned artifact UUID + title + comment; a note its text. Only these
    fields are recognized — image bytes, raw SVG and arbitrary URLs are
    rejected (422)."""

    model_config = {"extra": "forbid"}

    key: str = Field("", max_length=256)
    kind: str = ""
    artifact_id: uuid.UUID | None = None
    title: str = Field("", max_length=TITLE_LIMIT)
    comment: str = Field("", max_length=TEXT_LIMIT)
    text: str = Field("", max_length=TEXT_LIMIT)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in ("chart", "text"):
            raise ValueError("kind must be 'chart' or 'text'")
        return value

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "chart" and self.artifact_id is None:
            raise ValueError("a chart widget requires artifact_id")
        # a text note may be empty (the dashboard supports empty notes and the
        # picker exports them as-is)
        return self


class ReportIn(Schema):
    """What a generate call sends: the reporting period (a free label, e.g.
    "2026-Q3"), the output format, and — since B5 — the evaluations and
    sections to include. Empty evaluations means "whole project"; empty
    sections falls back to the project's report settings, then to all.
    Charts are referenced (dashboard + evaluation panels carry report-owned
    artifact UUIDs); inline image bytes and SVG are not accepted."""

    model_config = {"extra": "forbid"}

    report_pid: uuid.UUID | None = None
    period: str = Field("", max_length=PERIOD_LIMIT)
    format: str = "pdf"          # pdf | html
    evaluation_pids: list[uuid.UUID] = []
    sections: list[str] = []
    # the dashboard's collected widgets (charts + notes) as the webapp sends
    # them; empty means "no dashboard chapter"
    dashboard: list[DashboardWidgetRef] = []
    evaluation_charts: list[EvaluationChartExport] = []

    @field_validator("format")
    @classmethod
    def _format(cls, value: str) -> str:
        if value not in REPORT_FORMATS:
            raise ValueError("format must be 'pdf' or 'html'")
        return value


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
