"""Core AI-card versioning: the assessed system and its numbered versions.

The optional qualification domain owns the questionnaires and saved
qualifications; this module owns the shared system/version mechanism that core
and optional domains both reference.
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
        # rows is exactly what the pilot's trigger let an older "immutable"
        # version change again, so no version may be deleted at all.
        raise PublishedSnapshotError(
            "a card version is append-only and cannot be deleted"
        )

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.system} v{self.number}"
