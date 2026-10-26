"""`manage.py clean_old_secrets count|apply`: the engine's three stores, counted or cleaned, with the fields to
rotate by plugin and name, and never a value.

The default database holds r1 (a delivered configuration event with the judge key) and r3 (one already masked),
plugin A's configuration with its stored secrets, and e1 (a LangBiTe run whose failure text holds a key).
"""
import copy
import io
from datetime import datetime, timezone
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, LedgerOutbox, Plugin, PluginConfig,
                                 Project)
from aisc_backend.tests.test_config_secrets import KEY, SK, STORED, STORED_AFTER
from aisc_backend.tests.test_mask_configured_events import PID, PL, SETTINGS, configured
from aisc_backend.tests.test_scrub_stored_failures import LEAKED, SCRUBBED

STORE_LINES = {
    "count": ["default: aisc_backend_ledgeroutbox rows_to_mask=1",
              "default: aisc_backend_pluginconfig rows_to_change=1 fields_to_drop=4",
              "default: aisc_backend_evaluationplugin rows_to_scrub=1"],
    "apply": ["default: aisc_backend_ledgeroutbox rows_masked=1",
              "default: aisc_backend_pluginconfig rows_changed=1 fields_dropped=4",
              "default: aisc_backend_evaluationplugin rows_scrubbed=1"],
}
ROTATE_LINES = ["default: rotate plugin=aisc-plugin-a field=api_key configs=1",
                "default: rotate plugin=aisc-plugin-a field=hf_token configs=1",
                "default: rotate plugin=aisc-plugin-a field=judge.openai_api_key configs=1",
                # kept (its name is not secret) but a key was scrubbed out of it: listed by name all the same
                "default: rotate plugin=aisc-plugin-a field=prompt configs=1",
                "default: rotate plugin=aisc-plugin-a field=targets[0].auth_token configs=1",
                "default: rotate plugin=aisc-plugin-langbite failures=1",
                "default: rotate plugin=aisc-plugin-langbite field=judge_api_key events=1"]
TOTAL = {"count": "total: rows_to_mask=1 rows_to_change=1 fields_to_drop=4 rows_to_scrub=1",
         "apply": "total: rows_masked=1 rows_changed=1 fields_dropped=4 rows_scrubbed=1"}


class CleanOldSecrets(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", description="")
        langbite = Plugin.objects.create(project=project, name="LangBiTe", display_name="LangBiTe",
                                         package_name="aisc-plugin-langbite", version="0.2.8", pid=PL)
        a = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                  version="0.1.0")
        now = datetime.now(timezone.utc)
        self.r1 = LedgerOutbox.objects.create(project_pid=PID, delivered_at=now, event=configured(
            {"judge_api_key": KEY, "languages": ["en"]}, settings=SETTINGS))
        self.r3 = LedgerOutbox.objects.create(project_pid=PID, delivered_at=now, event=configured(
            {"judge_api_key": "set"}))
        self.config = PluginConfig.objects.create(plugin=a, config=copy.deepcopy(STORED))
        langbite_config = PluginConfig.objects.create(plugin=langbite, config={"languages": ["en"]})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Failed, project=project)
        self.e1 = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=langbite_config,
                                                  error_message=LEAKED)
        self.r3_event = copy.deepcopy(self.r3.event)

    def run_command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        call_command("clean_old_secrets", *args, stdout=out, stderr=err)
        return out.getvalue().splitlines(), err.getvalue()

    def stored(self):
        return (LedgerOutbox.objects.get(pk=self.r1.pk).event, PluginConfig.objects.get(pk=self.config.pk).config,
                EvaluationPlugin.objects.get(pk=self.e1.pk).error_message)

    def test_count_prints_the_counts_and_the_fields_to_rotate_and_changes_nothing(self):
        before = self.stored()
        lines, err = self.run_command("count")
        self.assertEqual(lines, STORE_LINES["count"] + ROTATE_LINES + [TOTAL["count"]])
        self.assertEqual(self.stored(), before)
        for value in (KEY, SK, "abc123"):
            self.assertNotIn(value, "\n".join(lines) + err)

    def test_apply_cleans_the_three_stores(self):
        lines, err = self.run_command("apply")
        self.assertEqual(lines, STORE_LINES["apply"] + ROTATE_LINES + [TOTAL["apply"]])
        event, config, failure = self.stored()
        self.assertEqual(event["content"]["config"], {"judge_api_key": "set", "languages": ["en"]})
        self.assertEqual(event["content"]["settings"], SETTINGS)
        self.assertEqual(config, STORED_AFTER)
        self.assertEqual(failure, SCRUBBED)
        self.assertEqual(LedgerOutbox.objects.get(pk=self.r3.pk).event, self.r3_event)
        for value in (KEY, SK, "abc123"):
            self.assertNotIn(value, "\n".join(lines) + err)

    def test_a_count_after_apply_finds_nothing(self):
        self.run_command("apply")
        lines, _ = self.run_command("count")
        self.assertEqual(lines, ["default: aisc_backend_ledgeroutbox rows_to_mask=0",
                                 "default: aisc_backend_pluginconfig rows_to_change=0 fields_to_drop=0",
                                 "default: aisc_backend_evaluationplugin rows_to_scrub=0",
                                 "total: rows_to_mask=0 rows_to_change=0 fields_to_drop=0 rows_to_scrub=0"])


class NoMigrationRewritesSecrets(TestCase):
    """Rewriting stored data needs a yes, and a migration runs on every start without asking: the cleanup is the
    command's, never a migration's."""

    def test_no_migration_runs_the_cleanup(self):
        folder = Path(__file__).resolve().parents[1] / "migrations"
        for path in sorted(folder.glob("0*.py")):
            text = path.read_text()
            for name in ("secret_cleanup", "config_secrets", "mask_outbox", "drop_config_secrets", "scrub_failures"):
                self.assertNotIn(name, text, f"{path.name} uses {name}")

