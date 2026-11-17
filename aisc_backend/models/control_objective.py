"""
Control objectives: the AI Act objective catalogue + the per-project risk
register and the risk-to-objective mapping (the risk & control matrix).

Native to the backend's single DB, project-FK-scoped like every other table.
Ports the reference control-objectives logic while keeping the model lean:

- the catalogue is seeded once (a data migration reads the vendored CSV into
  `ControlObjective`, ids O1..O50);
- each project has its own risk register (`Risk`), rated impact x likelihood
  by the assessor;
- `RiskObjectiveMapping` says which objectives mitigate which risks
  (manual-first: `source="person"`);
- an `ObjectiveProfile` holds the project's chosen profile (the objectives it
  picked, and which are flagged **key**).

Scoring (bands -> objective score -> proposed key objectives) is computed on
read in `services.control_objectives`, never stored, so a changed rating can
never leave a stale score.
"""
from django.db import models

from .common import Base
from .project import Project


class ControlObjective(Base):
    """One row of the AI Act control objectives catalogue (O1..O50).

    Keeps the reference columns verbatim; `assessment_mode`, the macro
    requirement and the note tag are what the matrix uses. Seeded once by a
    data migration; the catalogue is the same for every project (read-only).
    A project may add its own rows (`project` set): those are visible only in
    that project and editable there; the seeded rows (`project` NULL) are
    read-only everywhere.
    """

    #: O1 .. O50 for the seeded catalogue; a project's own code for a local row.
    #: Unique **per project** (and among the seeded rows), so two projects can
    #: each keep a local code of the same name without colliding with each other
    #: or with the shared catalogue.
    objective_id = models.CharField(max_length=32)
    macro_requirement = models.CharField(max_length=255)
    legal_basis = models.CharField(max_length=255, blank=True, default="")
    sub_requirement_label = models.CharField(max_length=255, blank=True, default="")
    text = models.TextField()
    assessment_mode = models.CharField(max_length=32)            # Control | Test | Control + Test
    target = models.CharField(max_length=255, blank=True, default="")
    standards_grounding = models.TextField(blank=True, default="")
    grounding_tier_flag = models.CharField(max_length=64, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    #: NULL for the seeded catalogue (read-only, every project); set for a
    #: project's own rows (visible and editable only in that project).
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE,
        related_name="custom_objectives",
    )

    class Meta:
        app_label = "aisc_backend"
        ordering = ["objective_id"]
        constraints = [
            # a local code is unique inside its project...
            models.UniqueConstraint(
                fields=["project", "objective_id"],
                name="control_objective_project_objective_id_uniq",
            ),
            # ...and the shared catalogue stays unique on its own (project NULL,
            # which a plain unique constraint over (project, id) does not cover).
            models.UniqueConstraint(
                fields=["objective_id"],
                condition=models.Q(project__isnull=True),
                name="control_objective_seeded_objective_id_uniq",
            ),
        ]

    @property
    def requires_control(self) -> bool:
        return "Control" in self.assessment_mode

    @property
    def requires_test(self) -> bool:
        return "Test" in self.assessment_mode

    @property
    def macro_id(self) -> str:
        """REQ1 from 'REQ1 Human Agency and Oversight'."""
        return self.macro_requirement.split(None, 1)[0]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.objective_id} {self.text[:40]}"


class Risk(Base):
    """One risk of a project's register, rated impact x likelihood by the person.

    Both ratings are 1..5; before both are set the risk counts as UNRATED (its
    rating must not be guessed as a default Medium).
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="risk_register"
    )
    position = models.PositiveIntegerField(default=1)
    impact = models.PositiveIntegerField(null=True, blank=True)
    likelihood = models.PositiveIntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default="")
    # The AIRO chain's text this risk names (from the AI card), if one does.
    airo_text = models.TextField(blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=("project", "position"), name="risk_project_position_uniq"
            ),
        ]

    @property
    def rating(self) -> int:
        """impact x likelihood (1..25), or 0 while either part is unrated."""
        if self.impact is None or self.likelihood is None:
            return 0
        return self.impact * self.likelihood

    def __str__(self):  # pragma: no cover - debug helper
        return f"risk {self.position} of {self.project.name}"


class RiskVersion(Base):
    """One saved revision of a risk's assessment, numbered 1..n.

    Append-only (like QuestionnaireVersion / SystemVersion): rating a risk
    creates a new row; the Risk columns stay the CURRENT head. Reading an old
    number returns the assessment as it was.
    """

    risk = models.ForeignKey(
        Risk, on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField()
    impact = models.PositiveIntegerField(null=True, blank=True)
    likelihood = models.PositiveIntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default="")
    airo_text = models.TextField(blank=True, default="")
    created_by = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("risk", "number"),
                name="risk_version_risk_number_uniq",
            ),
        ]
        ordering = ["number"]

    @property
    def rating(self) -> int:
        """impact x likelihood (1..25), or 0 while either part is unrated."""
        if self.impact is None or self.likelihood is None:
            return 0
        return self.impact * self.likelihood

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.risk} v{self.number}"


class RiskObjectiveMapping(Base):
    """'objective X mitigates risk Y', in the project's own register.

    Manual-first: `source` is "person" (added by the assessor); an AI
    suggestion is recorded as "ai" when one is wired in, as the reference does.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="risk_objective_mappings"
    )
    risk = models.ForeignKey(
        Risk, on_delete=models.CASCADE, related_name="objective_mappings"
    )
    objective = models.ForeignKey(
        ControlObjective, on_delete=models.CASCADE, related_name="risk_mappings"
    )
    quote = models.TextField(blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    source = models.CharField(max_length=8, default="person")   # person | ai

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("project", "risk", "objective"),
                name="risk_objective_mapping_uniq",
            ),
        ]

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.risk} <- {self.objective.objective_id}"


class ObjectiveProfile(Base):
    """A project's chosen objective profile: the objectives it works on, and
    which are flagged **key** (the assessor's choice; the default comes from
    the score on read)."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="objective_profiles"
    )
    objectives = models.JSONField(default=list)      # [objective_id, ...]
    key_objectives = models.JSONField(default=list)  # [objective_id, ...]
    #: Assessor-set per-objective values: {objective_id: {"score": float, "likelihood": int}}.
    objective_ratings = models.JSONField(default=dict)
    catalogue_digest = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        app_label = "aisc_backend"
        constraints = [
            models.UniqueConstraint(
                fields=("project", "name"), name="objective_profile_project_name_uniq"
            ),
        ]

    def __str__(self):  # pragma: no cover - debug helper
        return f"profile {self.name} of {self.project.name}"
