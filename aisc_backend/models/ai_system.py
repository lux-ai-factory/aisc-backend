from django.db import models

from .common import Base, HasData


class AIComponentType(models.TextChoices):
    """The kind of an ``AIComponent`` (ADR 0009): file, derived, reference, endpoint.

    ``LLM`` is retained only so the ``llm`` → ``endpoint`` data migration has a
    name to map from; new rows never use it.
    """

    DATASET = "dataset", "Dataset"
    MODEL = "model", "Model / file-backed artifact"
    DATASHAPE = "datashape", "DataShape (derived from a dataset)"
    RESOURCE = "resource", "Resource / generic reference"
    ENDPOINT = "endpoint", "Endpoint"
    LLM = "llm", "LLM (legacy, migrated to endpoint)"


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
    """Everything a run can use, as one model (ADR 0009).

    File components (``dataset``, ``model``) carry a stored artifact (the
    ``HasData`` fields); ``datashape`` carries its derived document and
    ``resource`` its reference in ``json_value``; an ``endpoint`` carries a URL, a
    protocol, an adapter id/version and that adapter's config as JSON, with its key
    in the project's secret store (``secret_key`` names it, never its value).

    A component belongs to a project, and optionally to the project's ``AISystem``
    (a project-level endpoint — a judge, the AI assistant's model — has no system).
    """

    project = models.ForeignKey(
        "Project", related_name="components", on_delete=models.CASCADE
    )
    system = models.ForeignKey(
        "AISystem", related_name="components", on_delete=models.CASCADE,
        null=True, blank=True,
    )
    component_type = models.CharField(
        max_length=50, choices=AIComponentType.choices, default=AIComponentType.MODEL
    )
    #: For a file component, the format derived from the stored name's suffix.
    format = models.CharField(max_length=32, blank=True, default="")

    # datashape components: which dataset component they were derived from,
    # plus the derived datashape document.
    source_dataset = models.ForeignKey(
        "self", related_name="derived_datashapes", on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    # Type-specific configuration for datashape (the document), resource (a
    # reference) and, on migration, an old llm's config.
    json_value = models.JSONField(blank=True, default=dict)

    # endpoint components (ADR 0009): a URL, a protocol and an adapter.
    url = models.CharField(max_length=500, blank=True, default="")
    protocol = models.CharField(max_length=64, blank=True, default="")
    adapter = models.CharField(max_length=255, blank=True, default="")
    adapter_version = models.CharField(max_length=32, blank=True, default="")
    adapter_config = models.JSONField(blank=True, default=dict)
    #: The preset form that made it (for reopening); not a mode.
    preset = models.CharField(max_length=64, blank=True, default="")
    #: The project config key holding the key/secret. Never the value.
    secret_key = models.CharField(max_length=255, blank=True, default="")

    @property
    def is_file_backed(self) -> bool:
        return self.component_type in {AIComponentType.DATASET, AIComponentType.MODEL}

    @property
    def is_endpoint(self) -> bool:
        return self.component_type == AIComponentType.ENDPOINT

    def save(self, *args, **kwargs):
        # A component's project is its system's project; a project-level
        # component sets `project` directly (ADR 0009).
        if self.project_id is None and self.system_id is not None:
            self.project_id = self.system.project_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name}"
