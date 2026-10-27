from django.db import models

from .common import Base, HasData


class AIComponentType(models.TextChoices):
    DATASET = "dataset", "Dataset"
    MODEL = "model", "Model / file-backed artifact"
    LLM = "llm", "LLM (OpenAI-compatible)"
    DATASHAPE = "datashape", "DataShape (derived from a dataset)"
    RESOURCE = "resource", "Resource / generic reference"


class AISystem(Base):
    """The assessed AI product/system. A project has exactly one of these.

    `Project` is the workspace/tenant; `AISystem` is the assessed target,
    composed of one or more `AIComponent`s.
    """

    project = models.OneToOneField(
        "Project", related_name="aisystem", on_delete=models.CASCADE
    )

    def get_components(self) -> list["AIComponent"]:
        """The components shown and offered as targets: not those moved to the platform as helper
        models. Uses the prefetched cache when available (async-safe); otherwise this must be called
        outside an async context or after prefetching."""
        return [c for c in self.components.all() if c.hidden_as_helper is None]

    def __str__(self):
        return f"{self.name}"


class AIComponent(HasData):
    """A functional component of an AISystem.

    `component_type` dispatches to type-specific behaviour. Only
    dataset/model/file-backed components carry a stored artifact (the `HasData`
    fields); the other types carry their configuration in `json_value`.
    """

    system = models.ForeignKey(
        "AISystem", related_name="components", on_delete=models.CASCADE
    )
    component_type = models.CharField(
        max_length=50, choices=AIComponentType.choices, default=AIComponentType.MODEL
    )

    # datashape components: which dataset component they were derived from,
    # plus the derived datashape document.
    source_dataset = models.ForeignKey(
        "self", related_name="derived_datashapes", on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    # Type-specific configuration, serialised per component type (e.g. a
    # DataShape for datashape, an LLMConfig for llm, a ResourceConfig for
    # resource).
    json_value = models.JSONField(blank=True, default=dict)
    # An `llm` component older runs used as a helper, moved to the platform's helper models
    # (manage.py move_helper_models): the reference it moved to. The row stays for those runs,
    # never listed with the system's components nor offered as a target.
    hidden_as_helper = models.CharField(max_length=200, null=True, blank=True)

    @property
    def is_file_backed(self) -> bool:
        return self.component_type in {
            AIComponentType.DATASET,
            AIComponentType.MODEL,
        }

    def __str__(self):
        return f"{self.name}"
