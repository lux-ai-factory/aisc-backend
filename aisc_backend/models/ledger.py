import json

from django.db import connections, models, router


def _through_functions(using: str) -> bool:
    """Whether the outbox of database `using` is written through its functions (a project database on
    Postgres, migration 0029): the engine's role may only read the table there."""
    connection = connections[using]
    if connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regprocedure('engine.ledger_outbox_append(uuid,jsonb)') IS NOT NULL")
        return bool(cursor.fetchone()[0])


class LedgerOutbox(models.Model):
    """An event about a change of this project, kept until the platform's ledger has it (aisc_backend.ledger).

    Written in the transaction of the change. Not one of the tables the readers are granted
    (migrate_projects.READER_GRANTS): an event can carry a plugin's configuration.

    Append-only in a project database on Postgres: a row is written by `engine.ledger_outbox_append`, marked once
    by `mark`, and its event changes only by masking a secret in it (`save(update_fields=["event"])`). Nothing
    else may change it, and nothing removes it. On sqlite the ORM writes it as any table.
    """

    project_pid = models.UUIDField()
    event = models.JSONField()
    queued_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # The platform's HTTP status when it refused the event: kept, and never sent again.
    refused = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.event.get('action')} {self.event.get('event_id')}"

    def save(self, *args, using=None, update_fields=None, **kwargs):
        using = using or router.db_for_write(type(self), instance=self)
        if not _through_functions(using):
            return super().save(*args, using=using, update_fields=update_fields, **kwargs)
        with connections[using].cursor() as cursor:
            if self._state.adding:
                cursor.execute("SELECT engine.ledger_outbox_append(%s, %s::jsonb)",
                               [str(self.project_pid), json.dumps(self.event)])
                self.pk = cursor.fetchone()[0]
                self._state.adding = False
                self._state.db = using
                if self.delivered_at is not None or self.refused is not None:
                    LedgerOutbox.mark(using, self.pk, delivered=self.delivered_at is not None, refused=self.refused)
                cursor.execute("SELECT queued_at, delivered_at FROM engine.aisc_backend_ledgeroutbox WHERE id = %s",
                               [self.pk])
                self.queued_at, self.delivered_at = cursor.fetchone()
                return None
            if update_fields is None or set(update_fields) != {"event"}:
                raise ValueError("a ledger outbox event is append-only: it is marked with LedgerOutbox.mark, and "
                                 "its event changes only by masking a secret in it")
            cursor.execute("SELECT engine.ledger_outbox_mask(%s, %s::jsonb)", [self.pk, json.dumps(self.event)])
        return None

    @classmethod
    def mark(cls, using: str, pk: int, *, delivered: bool, refused: int | None = None) -> bool:
        """Marks the event delivered (now) or refused (with `refused`, the platform's status), only while it has
        no mark; whether it did."""
        if _through_functions(using):
            with connections[using].cursor() as cursor:
                cursor.execute("SELECT engine.ledger_outbox_mark(%s, %s, %s)", [pk, delivered, refused])
                return bool(cursor.fetchone()[0])
        from datetime import datetime, timezone

        change = {"delivered_at": datetime.now(timezone.utc)} if delivered else {"refused": refused}
        return bool(cls.objects.using(using).filter(pk=pk, delivered_at__isnull=True, refused__isnull=True)
                    .update(**change))
