"""
Controls / compliance checklists, native to the backend's single DB.

The controls app is a service with no UI (like qualification): the webapp
renders, the backend owns every table. The catalogue is the centre — checklists
are listed by and installed from the backend's catalogue (§16), and the
AESIA/EUSAiR checklists are synced into the catalogue tables
(`ControlSource` / `ControlTemplate` / `ControlQuestion`) from the hosted
catalogue by `manage.py sync_controls_catalogue`. A project's install copies a
template into its own library (`ProjectChecklist`), answered through `Submission`
/ `SubmissionAnswer` with the reference lifecycle (Draft -> Closed -> reopen as
a new version, archive).

Scoring (coverage / readiness) is computed on read in
`services.controls_scoring`, never stored.
"""
import uuid

from django.db import models

from .common import Base
from .project import Project


class ControlSource(Base):
    """An authority (AESIA, EUSAiR) that published checklists."""

    slug = models.CharField(max_length=64, unique=True)
    citation = models.TextField(blank=True, default="")
    url = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"

    def __str__(self):  # pragma: no cover - debug helper
        return self.name


class ControlTemplate(Base):
    """One catalogue checklist (the seed data), installed into projects.

    `catalogue_id` is the reference's per-checklist slug; a project's checklist
    is a copy of this row + its questions (`ControlQuestion`).
    """

    catalogue_id = models.CharField(max_length=128, unique=True)
    source = models.ForeignKey(
        ControlSource, on_delete=models.RESTRICT, related_name="templates"
    )
    control_topic = models.CharField(max_length=255, blank=True, default="")
    country_ids = models.JSONField(default=list)
    regulation_ids = models.JSONField(default=list)
    source_updated_at = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        ordering = ["catalogue_id"]

    def __str__(self):  # pragma: no cover - debug helper
        return self.catalogue_id


class ControlQuestion(models.Model):
    """One ordered question of a catalogue checklist template."""

    template = models.ForeignKey(
        ControlTemplate, on_delete=models.CASCADE, related_name="questions"
    )
    order = models.PositiveIntegerField()
    text = models.TextField()
    article = models.CharField(max_length=64, blank=True, default="")
    category = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("template", "order"), name="control_question_template_order_uniq"
            ),
        ]
        ordering = ["order"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.template.catalogue_id} q{self.order}"


class ProjectChecklist(Base):
    """A checklist installed into a project's library (a copy of a template)."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="checklists"
    )
    template = models.ForeignKey(
        ControlTemplate, on_delete=models.RESTRICT, related_name="installed"
    )
    # Snapshot metadata at install time
    control_topic = models.CharField(max_length=255, blank=True, default="")
    country_ids = models.JSONField(default=list)
    regulation_ids = models.JSONField(default=list)
    source_updated_at = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("project", "template"),
                name="project_checklist_project_template_uniq",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.project.name} / {self.template.catalogue_id}"


class SubmissionStatus(models.TextChoices):
    Draft = 'Draft', 'Draft'
    Closed = 'Closed', 'Closed'


class Submission(models.Model):
    """An answered checklist, through the Draft -> Closed lifecycle.

    Reopening a closed submission creates the next version (the chain is a
    linked list via `previous`). Archiving hides it without losing it.
    """

    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=255, default="")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="submissions"
    )
    checklist = models.ForeignKey(
        ProjectChecklist, on_delete=models.CASCADE, related_name="submissions"
    )
    status = models.CharField(max_length=16,
                              choices=SubmissionStatus.choices,
                              default=SubmissionStatus.Draft)
    version = models.PositiveIntegerField(default=1)
    previous = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="next_version",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "aisc_backend"
        ordering = ["-created_at"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.checklist} v{self.version}"


class SubmissionAnswer(models.Model):
    """One question's answer on a submission: free text + a 1-5 score."""

    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="answers"
    )
    question = models.ForeignKey(
        ControlQuestion, on_delete=models.CASCADE, related_name="+"
    )
    answer = models.TextField(blank=True, default="")
    score = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("submission", "question"),
                name="submission_answer_submission_question_uniq",
            ),
        ]

    def __str__(self):  # pragma: no cover - debug helper
        return f"answer to q{self.question.order} on {self.submission}"
