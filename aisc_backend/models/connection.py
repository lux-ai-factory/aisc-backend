"""
Internal hosts a project may call (CON-01), as data.

An `AllowedHost` is an internal host a project's components may call: any public
address is reachable, an internal one only when listed here or allowed by the stack
for every project. Loopback, cloud metadata and this deployment's own services can
never be listed — `services.allowed_hosts` refuses them whatever this table says.

`Connection`, `Helper` and `RunBinding` are gone (ADR 0009): a thing a run can use
is an `AIComponent`, and what a run used is recorded on its `EvaluationInput`.
"""
from django.db import models

from .common import Base


class AllowedHost(Base):
    """An internal host this project's components may call (CON-01).

    Owners and admins keep this list. Only the *internal* class is listable: the
    deployment's own services, loopback and cloud metadata addresses are refused by
    `services.allowed_hosts` whatever this table says. `host` is either `host` or
    `host:port`, lower-cased; a change applies to the next call, with no restart.
    """

    project = models.ForeignKey(
        "Project", related_name="allowed_hosts", on_delete=models.CASCADE
    )
    host = models.CharField(max_length=255)
    note = models.CharField(max_length=255, blank=True, default="")
    #: The caller's stable subject — never a name (the ledger's convention).
    updated_by = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("project", "host"), name="allowed_host_project_host_uniq",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.project_id}:{self.host}"
