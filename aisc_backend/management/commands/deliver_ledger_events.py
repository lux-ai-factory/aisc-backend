"""`manage.py deliver_ledger_events`: send the engine's queued ledger events to the platform.

In the Configurator this command is what sends: the backend is not given the ledger token, so its
requests only queue their events, and the aisc-backend-ledger service runs this command. A process
that does hold the token also starts a pass for its project's database after each commit
(aisc_backend.ledger); this command then makes the passes those could not finish: the platform was
down, the process stopped before its thread ran. With `--every N` it makes one pass over every project
database each N seconds until it is stopped; without, one pass.

The databases are the ones migrate_projects lists, from pg_database. Output names databases and
counts only, never an event.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections

from aisc_backend import ledger, projectdb
from aisc_backend.management.commands.migrate_projects import LIST_DATABASES


class Command(BaseCommand):
    help = "Send the queued ledger events of every project database to the platform."

    def add_arguments(self, parser):
        parser.add_argument("--every", type=float, default=0,
                            help="seconds between passes, until stopped; 0 (the default) makes one pass")

    def handle(self, *args, every=0, **options):
        while True:
            self.make_pass()
            if not every:
                return
            time.sleep(every)

    def make_pass(self) -> None:
        send = ledger.http_sender()
        for alias in self._databases():
            try:
                if alias != DEFAULT_DB_ALIAS:
                    projectdb.open_alias(alias)
                sent, failed = ledger.deliver(alias, send)
                if sent or failed:
                    self.stdout.write(f"{alias}: {sent} sent, {failed} not sent")
            except (projectdb.NoSuchProjectDatabase, projectdb.SchemaNotProvisioned):
                continue
            except Exception as exc:  # noqa: BLE001 - reported by type only, then the next one
                self.stderr.write(f"failed {alias}: {type(exc).__name__}")
            finally:
                if alias != DEFAULT_DB_ALIAS and alias in connections.settings:
                    connections[alias].close()

    @staticmethod
    def _databases() -> list[str]:
        if not projectdb.enabled():
            return [DEFAULT_DB_ALIAS]
        platform = connections["platform"]
        try:
            with platform.cursor() as cursor:
                cursor.execute(LIST_DATABASES)
                names = [row[0] for row in cursor.fetchall()]
        finally:
            platform.close()
        return [projectdb.alias_for(projectdb.pid_of(name)) for name in names]
