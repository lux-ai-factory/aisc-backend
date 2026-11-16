"""
Qualification data, owned by the backend.

The qualification app is now a headless compute service: it answers "given
these form answers, here is the AI card" but stores nothing. Every qualification
table therefore lives HERE, in the single backend database, project-FK-scoped
like every other table.

The AI card's versioning mirrors the reference platform contract
(`core/system` -> the /system-versions endpoints): one System per project, and
numbered SystemVersion rows; only the latest version may change.
"""
from django.db import models

from .common import Base
from .project import Project


class System(Base):
    """The AI system a project qualifies: the subject of its AI card."""

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="qualification_system",
    )
    # Which questionnaire this project qualifies against (the backend's
    # per-project selection); null until one is chosen.
    questionnaire_id = models.CharField(max_length=36, blank=True, default="")

    def __str__(self):  # pragma: no cover - debug helper
        return f"system of {self.project.name}"


class Questionnaire(Base):
    """A questionnaire: the form a project answers. A JSON document.

    The questionnaire is a JSON form-schema (fields/blocks/extras) that the
    backend selects, adds, edits and removes; the headless qualification
    service builds a card from answers given against it.

    `project` is NULL for the service's DEFAULT questionnaires (available to
    every project); when set, the questionnaire belongs to that one project
    and is visible nowhere else.
    """
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="questionnaires",
        null=True,
        blank=True,
    )
    # definition: {"blocks": [...], "fields": [...], ...} — the form schema JSON
    definition = models.JSONField(default=dict)

    def __str__(self):  # pragma: no cover - debug helper
        return f"questionnaire {self.name}"


class QuestionnaireVersion(Base):
    """One saved revision of a questionnaire's form definition, numbered 1..n.

    Append-only (B1, like SystemVersion): every edit creates a new row; the
    Questionnaire.definition column stays the CURRENT head (the latest
    version's definition). Reading an old number returns the form as it was.
    """

    questionnaire = models.ForeignKey(
        Questionnaire,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    number = models.PositiveIntegerField()
    definition = models.JSONField(default=dict)
    created_by = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("questionnaire", "number"),
                name="questionnaire_version_questionnaire_number_uniq",
            ),
        ]
        ordering = ["number"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.questionnaire} v{self.number}"


class SystemVersion(Base):
    """One saved AI-card version of a project's system, numbered 1..n.

    Contract shape (matches the app's PlatformClient CardVersion):
    pid, project_id, number, name, version, provider, description,
    created_at, created_by. Only the latest version may change.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="system_versions",
    )
    system = models.ForeignKey(
        System,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=255, blank=True, default="")
    provider = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    created_by = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("system", "number"),
                name="system_version_system_number_uniq",
            ),
        ]
        ordering = ["number"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.system} v{self.number}"


class Qualification(Base):
    """One saved qualification. The assessment payload the backend owns.

    The form is the headless service's domain, so its fields are NOT mirrored
    as columns here (they would couple the single DB to the service's
    questionnaire schema): `answers` carries the form's content, `system_card`
    the built AI card the service returned, and `system_version` the numbered
    card version this qualification produced. JSONField keeps the model on
    SQLite in tests and Postgres in production.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="qualifications",
    )
    system_version = models.ForeignKey(
        SystemVersion,
        on_delete=models.CASCADE,
        related_name="qualifications",
        null=True,
        blank=True,
    )
    # Which questionnaire the answers were given against (the backend's
    # selection for the project at save time).
    questionnaire_id = models.CharField(max_length=36, blank=True, default="")
    # The questionnaire version number the answers were filled against.
    questionnaire_version = models.PositiveIntegerField(null=True, blank=True)
    answers = models.JSONField(default=dict)
    system_card = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=32, default="draft")

    def __str__(self):  # pragma: no cover - debug helper
        return f"qualification of {self.project.name}"
