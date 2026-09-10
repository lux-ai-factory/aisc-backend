from django.db import models

from .common import Base, HasData
from .project_config import ProjectConfig


class AIComponentType(models.TextChoices):
    DATASET = "dataset", "Dataset"
    MODEL = "model", "Model / file-backed artifact"
    LLM = "llm", "LLM (OpenAI-compatible)"
    REST = "rest", "REST endpoint"
    DATASHAPE = "datashape", "DataShape (derived from a dataset)"


class AISystem(Base):
    """The assessed AI product/system. A project has exactly one of these.

    `Project` is the workspace/tenant; `AISystem` is the assessed target,
    composed of one or more `AIComponent`s.
    """

    project = models.OneToOneField(
        "Project", related_name="aisystem", on_delete=models.CASCADE
    )

    def get_components(self) -> list["AIComponent"]:
        # Use the prefetched cache when available (async-safe); otherwise this
        # must be called outside an async context or after prefetching.
        return list(self.components.all())

    def __str__(self):
        return f"{self.name}"


class AIComponent(HasData):
    """A functional component of an AISystem.

    `component_type` dispatches to type-specific behaviour. Only
    dataset/model/file-backed components carry a stored artifact (the `HasData`
    fields); llm/rest components expose their data as an `EvaluationInput`.
    """

    system = models.ForeignKey(
        "AISystem", related_name="components", on_delete=models.CASCADE
    )
    component_type = models.CharField(
        max_length=50, choices=AIComponentType.choices, default=AIComponentType.MODEL
    )

    # openai-compatible / endpoint-backed components
    endpoint_url = models.CharField(max_length=500, blank=True, default="")
    secret = models.ForeignKey(
        "ProjectConfig", related_name="components", on_delete=models.SET_NULL,
        null=True, blank=True,
    )

    # datashape components: which dataset component they were derived from,
    # plus the derived datashape document.
    source_dataset = models.ForeignKey(
        "self", related_name="derived_datashapes", on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    json_value = models.JSONField(blank=True, default=dict)

    @property
    def is_file_backed(self) -> bool:
        return self.component_type in {
            AIComponentType.DATASET,
            AIComponentType.MODEL,
        }

    def __str__(self):
        return f"{self.name}"
