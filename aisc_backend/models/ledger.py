from django.db import models


class LedgerOutbox(models.Model):
    """An event about a change of this project, kept until the platform's ledger has it (aisc_backend.ledger).

    Written in the transaction of the change. Not one of the tables the readers are granted
    (migrate_projects.READER_GRANTS): an event can carry a plugin's configuration.
    """

    project_pid = models.UUIDField()
    event = models.JSONField()
    queued_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # The platform's HTTP status when it refused the event: kept, and never sent again.
    refused = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.event.get('action')} {self.event.get('event_id')}"
