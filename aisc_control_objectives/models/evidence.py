"""
The versioned evidence map: which tests and checklist answers stand as evidence
for which control objectives, on one published AI-card version.

Port of the pilot's evidence links (platform_service/evidence.py), re-expressed
on the single DB. `control_objectives` owns the objective-to-evidence links,
their notes and the independent review; `evaluation` owns runs/artifacts and
`controls` owns checklist answers. Those are read here through their ORM models
(I5: one DB, the owning app's model layer), never with raw SQL against another
app's tables.

A link belongs to one AI-card version (`SystemVersion`). Only the latest version
may change; an older version's map is kept exactly as it was. A successor version
carries the previous version's links forward as an offer, never silently.
"""
import uuid

from django.db import models

from .control_objective import ControlObjective
from aisc_backend.models.project import Project
from aisc_backend.models.system import SystemVersion


class EvidenceLink(models.Model):
    """'this test (or this checklist) is evidence for this objective' on a version.

    `kind` is "test" (key = the plugin's package name) or "control" (key = the
    installed checklist's pid). `note` is why it counts as evidence.
    """

    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="evidence_links"
    )
    version = models.ForeignKey(
        SystemVersion, on_delete=models.CASCADE, related_name="evidence_links"
    )
    objective = models.ForeignKey(
        ControlObjective, on_delete=models.CASCADE, related_name="evidence_links"
    )
    kind = models.CharField(max_length=16)          # test | control
    key = models.CharField(max_length=255)
    note = models.TextField(blank=True, default="")
    created_by = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "aisc_control_objectives"
        constraints = [
            models.UniqueConstraint(
                fields=("version", "objective", "kind", "key"),
                name="evidence_link_version_objective_kind_key_uniq",
            ),
        ]
        ordering = ["objective__objective_id", "kind", "key"]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.objective.objective_id} <- {self.kind}:{self.key}"


class EvidenceReview(models.Model):
    """The map of one version, marked reviewed by someone who made none of its links.

    One review per version; any later link change invalidates it (the map is no
    longer the one that was reviewed).
    """

    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="evidence_reviews"
    )
    version = models.OneToOneField(
        SystemVersion, on_delete=models.CASCADE, related_name="evidence_review"
    )
    reviewed_by = models.CharField(max_length=255)
    reviewed_by_name = models.CharField(max_length=255, blank=True, default="")
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "aisc_control_objectives"

    def __str__(self):  # pragma: no cover - debug helper
        return f"review of {self.version}"
