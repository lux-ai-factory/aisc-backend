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


class PublishedSnapshotError(RuntimeError):
    """A published snapshot (a completed qualification or a card version) is
    immutable: it is never updated or deleted, only superseded."""


class System(Base):
    """The AI system a project qualifies: the subject of its AI card.

    This is only optional content about the project's assessed system: it links
    to the core ``AISystem`` rather than naming a system of its own, so core and
    qualification can never resolve to two different assessed systems.
    """

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="qualification_system",
    )
    # The project's assessed-system identity (the core `AISystem`) this content
    # belongs to. Null until resolved; both sides are per-project, so resolving
    # it can only land on that one system.
    aisystem = models.ForeignKey(
        "AISystem",
        on_delete=models.SET_NULL,
        related_name="qualification_systems",
        null=True,
        blank=True,
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

    def delete(self, *args, **kwargs):
        # Append-only: a cut version is a published snapshot. Deleting the newest
        # rows is exactly how the pilot's trigger let an older "immutable"
        # version change again, so no version may be deleted at all.
        raise PublishedSnapshotError(
            "a card version is append-only and cannot be deleted"
        )

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
    # When this row was frozen as a published snapshot (an explicit publish),
    # or null while it is still an editable draft.
    published_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_published(self) -> bool:
        """A published snapshot: completed, and therefore immutable."""
        return self.status == "completed" or self.published_at is not None

    def _persisted_is_published(self) -> bool:
        if self.pk is None or self._state.adding:
            return False
        return Qualification.objects.filter(
            pk=self.pk, status="completed"
        ).exists()

    def save(self, *args, **kwargs):
        # A published snapshot is never mutated: an edit or a reopen must build a
        # draft/successor row instead (see `services.publication`).
        if self._persisted_is_published():
            raise PublishedSnapshotError(
                "a published qualification is immutable; edit it as a successor"
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._persisted_is_published():
            raise PublishedSnapshotError(
                "a published qualification cannot be deleted"
            )
        return super().delete(*args, **kwargs)

    def __str__(self):  # pragma: no cover - debug helper
        return f"qualification of {self.project.name}"


class CardComponentLink(Base):
    """Which of the engine's components an AI card's component is (pilot parity).

    Every save records, per card component, the engine component it stands for
    together with a **snapshot** of that component as it was then — so the
    system page can flag a component that changed in the engine since, and say
    which card component has no engine counterpart. The card component keeps a
    stable key from one version to the next (`component_key`), so a test on a
    component can be followed across versions.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="card_component_links"
    )
    qualification = models.ForeignKey(
        Qualification, on_delete=models.CASCADE, related_name="component_links"
    )
    #: The card component's stable key inside the card (name when it has none).
    component_key = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True, default="")
    component_type = models.CharField(max_length=64, blank=True, default="")
    role = models.CharField(max_length=255, blank=True, default="")
    engine_component = models.ForeignKey(
        "AIComponent",
        related_name="card_links",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    #: The engine component as it was when the link was made.
    snapshot = models.JSONField(default=dict)
    snapshot_digest = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("qualification", "component_key"),
                name="card_component_link_qualification_key_uniq",
            ),
        ]
        ordering = ["name", "component_key"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"card component {self.component_key} of {self.qualification.pid}"
