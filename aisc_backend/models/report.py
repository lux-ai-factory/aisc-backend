"""
Reports — a project's generated assessment report.

The backend is the single door for reporting: it owns the `Report` row (project
FK, the card-version ref it snapshots, the period, format and the stored file)
and calls the headless report service to render the PDF/HTML. The report
service is stateless (snapshot in -> document out, with its own service-token
door); it keeps no UI, no layout editor and no database of its own.
"""
import uuid

from django.db import models

from .project import Project
from .qualification import SystemVersion


class Report(models.Model):
    """One generated report for a project.

    `snapshot` keeps the assembled snapshot that produced the document (latest
    card-version ref + controls coverage + control-objectives priorities), so
    a report stays readable even if the project changes afterwards.
    """

    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="reports"
    )
    # The card version this report snapshots (the "version ref"); null if the
    # project has no AI-card version yet.
    system_version = models.ForeignKey(
        SystemVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    period = models.CharField(max_length=255, blank=True, default="")
    format = models.CharField(max_length=16, default="pdf")     # pdf | html
    # The report's own version within the project (1, 2, 3, …): the cover's
    # "Version" is the report's, not the AI card's.
    version = models.PositiveIntegerField(default=1)
    # Where the rendered file lives (a storage reference; the backend holds the
    # bytes of `document` and `file_ref` names the key for a future object store).
    file_ref = models.CharField(max_length=255, blank=True, default="")
    snapshot = models.JSONField(default=dict)
    # The rendered document bytes (PDF, or the HTML page); stored by the backend
    # when the headless service answers. Null while generation has not produced
    # a document (e.g. the service is absent in dev/tests -> the HTML fallback).
    document = models.BinaryField(null=True, blank=True)
    status = models.CharField(max_length=32, default="generated")
    generated_by = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "aisc_backend"
        ordering = ["-created_at"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"report {self.pid} of {self.project.name}"


class ProjectReportSettings(models.Model):
    """A project's report defaults (B5): the format and sections a generate
    call falls back to when it does not pick its own."""

    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name="report_settings"
    )
    default_format = models.CharField(max_length=16, default="pdf")  # pdf | html
    default_sections = models.JSONField(default=list)  # subset of REPORT_SECTIONS, [] = all
    # the report's customisation: what the cover says and the document's colours
    report_title = models.CharField(max_length=200, blank=True, default="")
    subtitle = models.CharField(max_length=300, blank=True, default="")
    primary_color = models.CharField(max_length=7, default="#000fdf")
    accent_color = models.CharField(max_length=7, default="#ff007e")
    # the full look contract (the renderer's `style`): the typeface, the
    # page-margin texts, the confidentiality marking, the document id and a logo
    font = models.CharField(max_length=32, default="inter")
    font_size_pt = models.FloatField(default=11)
    header_text = models.CharField(max_length=120, blank=True, default="")
    footer_text = models.CharField(max_length=120, blank=True, default="")
    marking = models.CharField(max_length=32, default="none")
    show_document_id = models.BooleanField(default=False)
    logo = models.JSONField(null=True, blank=True, default=None)  # {mime, data_base64}
    updated_by = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "aisc_backend"

    def __str__(self):  # pragma: no cover - debug helper
        return f"report settings of {self.project.name}"
