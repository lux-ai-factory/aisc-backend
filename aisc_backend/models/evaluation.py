import uuid
from django.db import models

from .common import CreatedAtModel
from .observation import Observation
from .plugin import EvaluationPlugin


class EvaluationStatus(models.TextChoices):
    Done = "Done", "Done"
    Failed = "Failed", "Failed"
    Archived = "Archived", "Archived"
    Pending = "Pending", "Pending"
    Processing = "Processing", "Processing"
    Custom = "Custom", "Custom"


class Evaluation(CreatedAtModel):
    pid = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=255, choices=EvaluationStatus.choices)

    project = models.ForeignKey(
        "Project", related_name="evaluations", on_delete=models.PROTECT
    )

    task = models.UUIDField(default=None, null=True, blank=True)

    def get_observations(self) -> list[Observation]:
        return list(self.observations.all())

    def get_evaluation_plugins(self) -> list[EvaluationPlugin]:
        return list(self.evaluation_plugins.all())

    def get_ai_systems(self) -> list[dict]:
        """Flatten the AI systems assessed by this evaluation and their components.

        Traces each EvaluationPlugin run through its inputs to the AIComponent
        they reference, then groups those components by their owning AISystem.
        Returns a list of ``{"aisystem": AISystem, "components": [AIComponent]}``.
        """
        systems: dict = {}
        for evaluation_plugin in self.evaluation_plugins.all():
            for input_ in evaluation_plugin.get_inputs():
                component = input_.component
                system = component.system
                entry = systems.setdefault(system.pid, {"aisystem": system, "components": []})
                if not any(existing.pid == component.pid for existing in entry["components"]):
                    entry["components"].append(component)
        return list(systems.values())

    def __str__(self):
        return f"{self.pid} ({self.status})"
