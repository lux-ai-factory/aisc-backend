"""`manage.py reference_conversation_dimensions count|apply [--project PID ...]`: conversation text in the
dimensions of measures stored before the engine kept trace references.

`count` reports how many measures hold conversation text and changes nothing; `apply` replaces each such value
with its reference to the recorded call (`trace:<test pid>#sha256=<digest>`, conversation_dimensions.py), through
`engine.measurement_dimensions_referenced`, which takes that change and nothing else. A one-off, run only with
the owner's yes, after a backup: the hash of a changed measure's dimensions then differs from the one the
ledger froze, so the change is declared to the ledger (operator.correction.declared) by the operator.

The databases are those deliver_ledger_events serves; `--project` narrows them. Output names databases and
counts only, never a value or a DSN. Postgres project databases only.
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from aisc_backend import conversation_dimensions, projectdb
from aisc_backend.management.commands.deliver_ledger_events import Command as DeliverCommand

WORDS = {"count": "measures_to_reference", "apply": "measures_referenced"}

ROWS = """
SELECT m.id, m.dimensions, t.pid
  FROM engine.aisc_backend_measurement m
  JOIN engine.aisc_backend_observation o ON o.id = m.observation_id
  JOIN engine.aisc_backend_evaluationplugin t ON t.id = o.evaluation_plugin_id
 WHERE jsonb_typeof(m.dimensions) = 'object'
 ORDER BY m.id
"""


def with_references(dimensions: dict, test) -> dict:
    """The dimensions with every conversation text replaced by its reference; values of other types stay."""
    out = {}
    for key, value in dimensions.items():
        if (key.casefold() in conversation_dimensions.CONVERSATION_KEYS and isinstance(value, str) and value
                and not conversation_dimensions.is_reference(value)):
            value = conversation_dimensions.reference(test, value)
        out[key] = value
    return out


class Command(BaseCommand):
    help = "Count (count) or replace (apply) conversation text stored in measures' dimensions."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=sorted(WORDS), help="count: report only; apply: replace")
        parser.add_argument("--project", action="append", default=[], metavar="PID",
                            help="only this project's database (repeatable)")

    def handle(self, *args, action, project=(), **options):
        if not projectdb.enabled():
            raise CommandError("reference_conversation_dimensions needs the project databases (DB_ENGINE postgresql)")
        word, total, failed = WORDS[action], 0, 0
        aliases = [projectdb.alias_for(pid) for pid in project] if project else DeliverCommand._databases()
        for alias in aliases:
            try:
                projectdb.open_alias(alias)
                found = self._reference(connections[alias], action == "apply")
            except (projectdb.NoSuchProjectDatabase, projectdb.SchemaNotProvisioned):
                continue
            except Exception as exc:  # noqa: BLE001 - reported by type only, then the next one
                self.stderr.write(f"failed {alias}: {type(exc).__name__}")
                failed += 1
                continue
            finally:
                if alias in connections.settings:
                    connections[alias].close()
            self.stdout.write(f"{alias}: {word}={found}")
            total += found
        self.stdout.write(f"total: {word}={total}")
        if failed:
            raise CommandError(f"{failed} project database(s) failed")

    @staticmethod
    def _reference(connection, apply: bool) -> int:
        found = 0
        with connection.cursor() as cursor:
            cursor.execute(ROWS)
            rows = cursor.fetchall()
            for measurement_id, dimensions, test in rows:
                if isinstance(dimensions, str):
                    dimensions = json.loads(dimensions)
                new = with_references(dimensions, test)
                if new == dimensions:
                    continue
                found += 1
                if apply:
                    cursor.execute("SELECT engine.measurement_dimensions_referenced(%s, %s::jsonb)",
                                   [measurement_id, json.dumps(new)])
        return found
