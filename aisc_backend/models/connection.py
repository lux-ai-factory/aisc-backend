"""
Connections, adapters and helper models (CON-01), as data.

A `Connection` is how a test reaches a system or a component: a protocol
(`kind`, one of the four built-in adapters rest/openai/a2a/oip plus any installed
adapter's own kind), the endpoint, and the protocol's own fields. Its key and
secret headers live in the project's encrypted secret store (`ProjectConfig`,
category `secrets`) — never here and never in a `json_value`.

A `Helper` is a model a test uses as a tool (a judge, an attacker, a grader, an
embedder, a generator): one endpoint, one key, bound to the roles it serves.

A `RunBinding` records, for one run, which adapter/version and which helper a role
used. It is a *snapshot*: editing a connection after the run does not rewrite what
the run recorded.
"""
from django.db import models

from .common import Base


class ConnectionKind(models.TextChoices):
    """The built-in protocols. Installed adapters add their own kinds."""

    REST = "rest", "REST API"
    OPENAI = "openai", "OpenAI-compatible"
    A2A = "a2a", "A2A agent"
    OIP = "oip", "Open Inference Protocol"


class HelperRole(models.TextChoices):
    JUDGE = "judge", "judge"
    ATTACKER = "attacker", "attacker"
    GRADER = "grader", "grader"
    EMBEDDER = "embedder", "embedder"
    GENERATOR = "generator", "generator"


class Connection(Base):
    """One endpoint a test reaches, with the protocol that reaches it."""

    project = models.ForeignKey(
        "Project", related_name="connections", on_delete=models.CASCADE
    )
    #: What a person reads in a list; `name` is the handle that identifies it.
    label = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(
        max_length=32, choices=ConnectionKind.choices, default=ConnectionKind.OPENAI
    )
    #: CharField, not URLField: a connection may name an internal host, which a
    #: URL field's validator would refuse before the allowlist is consulted.
    base_url = models.CharField(max_length=500, blank=True, default="")

    # openai / oip
    model = models.CharField(max_length=255, blank=True, default="")
    supports_tools = models.BooleanField(default=False)
    supports_logprobs = models.BooleanField(default=False)

    # a2a / rest
    protocol_version = models.CharField(max_length=16, blank=True, default="")
    path = models.CharField(max_length=500, blank=True, default="")

    # rest
    method = models.CharField(max_length=8, blank=True, default="")
    body_template = models.JSONField(null=True, blank=True)
    response_path = models.CharField(max_length=255, blank=True, default="")
    refusal = models.JSONField(null=True, blank=True)

    # plain headers (JSON); a secret header's *name* is listed here so a listing
    # can show it without its value
    headers = models.JSONField(blank=True, default=dict)
    secret_header = models.CharField(max_length=255, blank=True, default="")

    timeout_s = models.PositiveIntegerField(default=60)
    #: An installed adapter's own non-secret fields, by name.
    settings = models.JSONField(blank=True, default=dict)

    #: What it reaches: a component of the project's system, or nothing (the
    #: system itself). Set once, when the connection is first made.
    target_component = models.ForeignKey(
        "AIComponent",
        related_name="connections",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_ok = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("project", "name"), name="connection_project_name_uniq",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.project_id}:{self.name} ({self.kind})"


class Helper(Base):
    """A model a test uses as a tool, bound to the roles it serves."""

    project = models.ForeignKey(
        "Project", related_name="helpers", on_delete=models.CASCADE
    )
    label = models.CharField(max_length=120, blank=True, default="")
    endpoint_url = models.CharField(max_length=500)
    #: The roles it may be picked for; see HelperRole.
    roles = models.JSONField(blank=True, default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("project", "name"), name="helper_project_name_uniq",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.project_id}:{self.name} ({','.join(self.roles or [])})"


class RunBinding(Base):
    """What one run used: an adapter/version for the assessed system, and the
    helper a role resolved to. Snapshotted, so the record is what ran."""

    evaluation_plugin = models.ForeignKey(
        "EvaluationPlugin",
        related_name="connection_bindings",
        on_delete=models.CASCADE,
    )
    #: Empty for the assessed connection; a HelperRole for a helper.
    role = models.CharField(max_length=64, blank=True, default="")
    connection = models.ForeignKey(
        Connection, related_name="run_bindings", on_delete=models.SET_NULL, null=True, blank=True,
    )
    helper = models.ForeignKey(
        Helper, related_name="run_bindings", on_delete=models.SET_NULL, null=True, blank=True,
    )
    #: The snapshot: the adapter as it was when the run bound it.
    kind = models.CharField(max_length=32, blank=True, default="")
    adapter_version = models.CharField(max_length=16, blank=True, default="")
    base_url = models.CharField(max_length=500, blank=True, default="")
    model = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("evaluation_plugin", "role"),
                name="run_binding_plugin_role_uniq",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.evaluation_plugin_id}:{self.role or 'assessed'} ({self.kind})"
