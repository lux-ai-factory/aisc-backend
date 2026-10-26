"""`manage.py clean_old_secrets count|apply [--project PID ...]`: old plaintext plugin secrets in the engine's stores.

`count` reports what `apply` would change and changes nothing; `apply` masks the configuration events of the
outbox, drops the secret fields of the stored plugin configurations and scrubs the failure text of plugin runs
(aisc_backend.secret_cleanup). Both list the fields whose keys were stored in clear and must be rotated, by
plugin and name.

The databases are those deliver_ledger_events serves (every project database, or `default` when project
databases are off); `--project` narrows them. Output names databases, plugins, fields and counts only: never a
value, a DSN or a row. Run by scripts/clean-old-secrets.sh, which backs the tables up first.
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections

from aisc_backend import projectdb, secret_cleanup
from aisc_backend.management.commands.deliver_ledger_events import Command as DeliverCommand
from aisc_backend.models import EvaluationPlugin, LedgerOutbox, PluginConfig

WORDS = {
    "count": ("rows_to_mask", "rows_to_change", "fields_to_drop", "rows_to_scrub"),
    "apply": ("rows_masked", "rows_changed", "fields_dropped", "rows_scrubbed"),
}


class Command(BaseCommand):
    help = "Count (count) or clean (apply) old plaintext plugin secrets in the engine's stores."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=sorted(WORDS), help="count: report only; apply: clean")
        parser.add_argument("--project", action="append", default=[], metavar="PID",
                            help="only this project's database (repeatable)")

    def handle(self, *args, action, project=(), **options):
        apply = action == "apply"
        mask, change, drop, scrub = WORDS[action]
        totals = Counter()
        failed = 0
        for alias in self._databases(project):
            try:
                if alias != DEFAULT_DB_ALIAS:
                    projectdb.open_alias(alias)
                # The project router sends every ORM query, the saves included, to the admitted alias.
                token = projectdb.admitted.set(alias)
                try:
                    found = self._clean(alias, apply)
                finally:
                    projectdb.admitted.reset(token)
            except (projectdb.NoSuchProjectDatabase, projectdb.SchemaNotProvisioned):
                continue
            except Exception as exc:  # noqa: BLE001 - reported by type only, then the next one
                self.stderr.write(f"failed {alias}: {type(exc).__name__}")
                failed += 1
                continue
            finally:
                if alias != DEFAULT_DB_ALIAS and alias in connections.settings:
                    connections[alias].close()
            masked, configs, fields, scrubbed, rotate = found
            self.stdout.write(f"{alias}: aisc_backend_ledgeroutbox {mask}={masked}")
            self.stdout.write(f"{alias}: aisc_backend_pluginconfig {change}={configs} {drop}={fields}")
            self.stdout.write(f"{alias}: aisc_backend_evaluationplugin {scrub}={scrubbed}")
            for line in rotate:
                self.stdout.write(f"{alias}: {line}")
            totals.update({mask: masked, change: configs, drop: fields, scrub: scrubbed})
        self.stdout.write("total: " + " ".join(f"{word}={totals[word]}" for word in WORDS[action]))
        if failed:
            raise CommandError(f"{failed} project database(s) failed")

    @staticmethod
    def _databases(projects) -> list[str]:
        if projects:
            return [projectdb.alias_for(pid) for pid in projects]
        return DeliverCommand._databases()

    @staticmethod
    def _clean(alias: str, apply: bool):
        masked, events = secret_cleanup.mask_outbox(LedgerOutbox, alias, apply)
        configs, dropped = secret_cleanup.drop_config_secrets(PluginConfig, alias, apply)
        scrubbed, failures = secret_cleanup.scrub_failures(EvaluationPlugin, alias, apply)
        packages = secret_cleanup.packages_by_pid(LedgerOutbox, alias, {item_id for item_id, _ in events})
        rotate = Counter()
        for package, path in dropped:
            rotate[(package, path, "configs")] += 1
        for item_id, path in events:
            rotate[(packages.get(item_id, f"unknown:{item_id}"), path, "events")] += 1
        for package in failures:
            rotate[(package, "", "failures")] += 1
        lines = [f"rotate plugin={package} field={path} {kind}={n}" if path else f"rotate plugin={package} {kind}={n}"
                 for (package, path, kind), n in sorted(rotate.items())]
        return masked, configs, len(dropped), scrubbed, lines
