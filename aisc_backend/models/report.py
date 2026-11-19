"""
Reports — a project's generated assessment report and its layouts.

The backend is the single door for reporting: it owns the `ReportLayout` rows
(ordered blocks) and the `Report` row (project FK, the card-version ref it
snapshots, the layout + revision, the period, format and the stored document).
Rendering happens in this app (`services.report_render`, the one engine, ADR 0008)
and produces a real PDF, Word or HTML document; there is no external renderer and
an HTML page is never served as a PDF.
"""
import uuid

from django.db import models

from .project import Project
from .qualification import SystemVersion


class ReportLayout(models.Model):
    """A project's report layout: an ordered list of blocks, an index and
    numbering (the pilot's composer layout, folded into this app).

    A layout holds no project data: the AI-card version, period and comparison
    are chosen when a report is generated and recorded on the `Report`. The
    five built-in layouts are code, not rows (`services.report_blocks`), so they
    are shared read-only by every project and can only be duplicated.
    """

    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="report_layouts"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    #: Ordered blocks: [{"instance_id": "<uuid>", "block_type": "<id>", "options": {...}}].
    blocks = models.JSONField(default=list)
    show_index = models.BooleanField(default=True)
    numbering = models.BooleanField(default=True)
    #: Bumped on every save so a generate call can refuse a layout that changed
    #: while the composer was looking at it.
    revision = models.PositiveIntegerField(default=1)
    created_by = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "aisc_backend"
        ordering = ["name", "created_at"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"report layout {self.name} of {self.project.name}"


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
    # The layout (and its revision) this report was generated from. SET_NULL so a
    # deleted layout does not delete its historical reports; the snapshot keeps
    # the blocks, so an old report still renders.
    layout = models.ForeignKey(
        ReportLayout,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    layout_revision = models.PositiveIntegerField(null=True, blank=True)
    period = models.CharField(max_length=255, blank=True, default="")
    format = models.CharField(max_length=16, default="pdf")     # pdf | docx | html
    # The report's own version within the project (1, 2, 3, …): the cover's
    # "Version" is the report's, not the AI card's.
    version = models.PositiveIntegerField(default=1)
    # Where the rendered file lives (a storage reference; the backend holds the
    # bytes of `document` and `file_ref` names the key for a future object store).
    file_ref = models.CharField(max_length=255, blank=True, default="")
    # The immutable generation snapshot: the project's ORM data (version ref,
    # controls coverage, objectives matrix, evaluations, layout + blocks) frozen
    # at generation time. Reads never recompute it, so an old report does not
    # change when the project does.
    snapshot = models.JSONField(default=dict)
    #: sha256 of the canonical snapshot, so tampering/a drift is detectable.
    snapshot_checksum = models.CharField(max_length=64, blank=True, default="")
    # The rendered document bytes (a real PDF or DOCX); null while generation has
    # not produced a document. `document_pdf` holds the PDF companion of a Word
    # report (the pilot's "a Word report also gets a PDF copy").
    document = models.BinaryField(null=True, blank=True)
    document_pdf = models.BinaryField(null=True, blank=True)
    #: sha256 of the primary document bytes.
    document_checksum = models.CharField(max_length=64, blank=True, default="")
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
    default_format = models.CharField(max_length=16, default="pdf")  # pdf | docx | html
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
