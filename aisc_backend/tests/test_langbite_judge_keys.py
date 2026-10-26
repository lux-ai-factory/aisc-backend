"""A LangBiTe configuration saved while its form still asked for the judge loses its judge fields in the cleanup,
behind the cleanup's backup and dry run; migration 0024 changes nothing.

LangBiTe's form once had judge_model and judge_api_key; the judge is now a helper model picked in the evaluation
form, so nothing could clear a value saved then. `manage.py clean_old_secrets` retires both fields of every stored
LangBiTe configuration: a judge key in clear is listed to rotate, as every stored key is; what is left of the two
fields is listed as retired. Other plugins' fields of the same names follow the general rule only. Migration 0024
stays in the chain, as the leaf, with no operation: an install that ran its earlier version keeps it applied.
"""
import importlib
import io
import logging
import re

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from aisc_backend.models import Plugin, PluginConfig, Project

MIGRATION = importlib.import_module("aisc_backend.migrations.0024_drop_stored_langbite_judge_keys")
KEY = "sk-judge-0123456789abcdefghij"
LANGBITE = "aisc-plugin-langbite"
STORED = {"llm_eval": True, "judge_model": "gpt-4o-mini", "judge_api_key": KEY, "languages": ["en"]}
OTHER = {"judge_model": "kept", "judge_api_key": "kept-too"}
ROTATE = f"default: rotate plugin={LANGBITE} field=judge_api_key configs=1"
RETIRED_MODEL = f"default: retired plugin={LANGBITE} field=judge_model configs=1"
RETIRED_KEY = f"default: retired plugin={LANGBITE} field=judge_api_key configs=1"
CONFIG_LINE = re.compile(r"^default: aisc_backend_pluginconfig (rows_to_change|rows_changed)=(\d+) "
                         r"(fields_to_drop|fields_dropped)=(\d+)$", re.MULTILINE)


def cleanup(action):
    out = io.StringIO()
    call_command("clean_old_secrets", action, stdout=out)
    return out.getvalue()


def config_counts(output):
    [(_, rows, _, fields)] = CONFIG_LINE.findall(output)
    return int(rows), int(fields)


def langbite_lines(output):
    return [line for line in output.splitlines() if f"plugin={LANGBITE}" in line]


class LangbiteJudgeFields(TransactionTestCase):
    def setUp(self):
        project = Project.objects.create(name="p", description="")
        self.langbite = Plugin.objects.create(project=project, name="LangBiTe", display_name="LangBiTe",
                                              package_name=LANGBITE, version="0.2.8")
        other = Plugin.objects.create(project=project, name="Other", display_name="Other",
                                      package_name="aisc-plugin-other", version="0.1.0")
        self.saved = PluginConfig.objects.create(plugin=self.langbite, config=dict(STORED))
        self.other = PluginConfig.objects.create(plugin=other, config=dict(OTHER))

    # The migration

    def test_the_migration_has_no_operation_and_stays_on_0023(self):
        self.assertEqual(MIGRATION.Migration.operations, [])
        self.assertEqual(MIGRATION.Migration.dependencies, [("aisc_backend", "0023_ledger_outbox")])
        self.assertFalse(hasattr(MIGRATION, "forwards"))

    def test_migrating_to_0024_changes_no_row(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("aisc_backend", "0023_ledger_outbox")])
        executor = MigrationExecutor(connection)
        executor.migrate([("aisc_backend", "0024_drop_stored_langbite_judge_keys")])
        self.saved.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.saved.config, STORED)
        self.assertEqual(self.other.config, OTHER)

    # The cleanup

    def test_count_lists_the_key_to_rotate_and_the_retired_field_and_changes_nothing(self):
        output = cleanup("count")
        self.assertIn(ROTATE, output.splitlines())
        self.assertIn(RETIRED_MODEL, output.splitlines())
        self.assertNotIn(KEY, output)
        with_row = config_counts(output)
        self.saved.refresh_from_db()
        self.assertEqual(self.saved.config, STORED)

        self.saved.delete()
        without_row = config_counts(cleanup("count"))
        self.assertEqual((with_row[0] - without_row[0], with_row[1] - without_row[1]), (1, 2))

    def test_apply_removes_both_fields_and_a_second_count_finds_nothing(self):
        before = config_counts(cleanup("count"))
        output = cleanup("apply")
        self.assertIn(ROTATE, output.splitlines())
        self.assertIn(RETIRED_MODEL, output.splitlines())
        self.assertEqual(config_counts(output), before)
        self.saved.refresh_from_db()
        self.assertEqual(self.saved.config, {"llm_eval": True, "languages": ["en"]})

        again = cleanup("count")
        self.assertEqual(langbite_lines(again), [])
        self.assertEqual(config_counts(again)[0], before[0] - 1)

    def test_another_plugins_judge_model_stays(self):
        cleanup("apply")
        self.other.refresh_from_db()
        self.assertEqual(self.other.config.get("judge_model"), "kept")

    def test_empty_judge_fields_are_retired_and_not_rotated(self):
        self.saved.config = {"judge_api_key": "", "judge_model": ""}
        self.saved.save()
        output = cleanup("count")
        self.assertIn(RETIRED_KEY, output.splitlines())
        self.assertIn(RETIRED_MODEL, output.splitlines())
        self.assertNotIn(ROTATE, output.splitlines())

    def test_a_configuration_the_earlier_migration_already_cleared_is_not_counted(self):
        self.saved.config = {"llm_eval": True, "languages": ["en"]}
        self.saved.save()
        with_row = config_counts(cleanup("count"))
        output = cleanup("count")
        self.assertEqual(langbite_lines(output), [])
        self.saved.delete()
        self.assertEqual(config_counts(cleanup("count")), with_row)

    def test_the_key_is_never_printed_or_logged(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            outputs = cleanup("count") + cleanup("apply")
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        self.assertNotIn(KEY, outputs)
        self.assertNotIn(KEY, stream.getvalue())

    def test_the_retired_lines_come_after_the_rotate_lines(self):
        lines = cleanup("count").splitlines()
        self.assertLess(lines.index(ROTATE), lines.index(RETIRED_MODEL))
