"""
Project membership: who may see and change a project, as data.

One row per account per project; roles are `viewer` | `editor` | `owner`.
Decisions key on the Keycloak **subject** (stable, unlike an email); `email` is
only for showing a member list, never read by a decision.

Membership is data rather than a realm role: there is one of these per project
per person, and as roles the shape of the work would leak into the identity
provider, where no module could see it without asking.
"""
from django.db import models

from .common import CreatedAtModel
from .project import Project


class MembershipRole(models.TextChoices):
    Viewer = 'viewer', 'viewer'
    Editor = 'editor', 'editor'
    Owner = 'owner', 'owner'


class ProjectMember(CreatedAtModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="members",
    )
    # Keycloak's subject. Stable: an email can be changed, and a decision that
    # keyed on one would follow the new owner of the address.
    subject = models.CharField(max_length=255)
    # for showing a list of members to a person; never what a decision reads
    email = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(
        max_length=16,
        choices=MembershipRole.choices,
        default=MembershipRole.Viewer,
    )

    class Meta:
        app_label = "aisc_backend"
        # one row per person per project; add/update is an upsert on this pair
        constraints = [
            models.UniqueConstraint(
                fields=("project", "subject"),
                name="project_member_project_subject_uniq",
            ),
        ]
        indexes = [models.Index(fields=("subject",), name="project_member_subject_idx")]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.subject} is {self.role} of {self.project_id}"
