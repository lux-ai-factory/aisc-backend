"""A LangBiTe configuration saved while its form still asked for the judge keeps no judge key.

LangBiTe's form once had judge_model and judge_api_key; the judge is now a helper model picked in the
evaluation form, the form no longer has those fields, so nothing could clear a key saved then.
Migration 0024 removes both from every stored LangBiTe configuration and leaves other plugins' alone.
"""
import importlib
import io
import logging

from django.apps import apps as django_apps
from django.db import connection
from django.test import TransactionTestCase

from aisc_backend.models import Plugin, PluginConfig, Project

MIGRATION = importlib.import_module("aisc_backend.migrations.0024_drop_stored_langbite_judge_keys")
KEY = "sk-judge-0123456789abcdefghij"


class StoredJudgeKeys(TransactionTestCase):
    def setUp(self):
        project = Project.objects.create(name="p", description="")
        langbite = Plugin.objects.create(project=project, name="LangBiTe", display_name="LangBiTe",
                                         package_name="aisc-plugin-langbite", version="0.2.8")
        other = Plugin.objects.create(project=project, name="Other", display_name="Other",
                                      package_name="aisc-plugin-other", version="0.1.0")
        self.saved = PluginConfig.objects.create(plugin=langbite, config={
            "llm_eval": True, "judge_model": "gpt-4o-mini", "judge_api_key": KEY, "languages": ["en"]})
        self.other = PluginConfig.objects.create(plugin=other, config={
            "judge_model": "kept", "judge_api_key": "kept-too"})

    def migrate(self):
        with connection.schema_editor() as editor:
            MIGRATION.forwards(django_apps, editor)

    def test_the_judge_fields_go_and_the_rest_of_the_configuration_stays(self):
        self.migrate()
        self.saved.refresh_from_db()
        self.assertEqual(self.saved.config, {"llm_eval": True, "languages": ["en"]})

    def test_another_plugins_configuration_is_untouched(self):
        self.migrate()
        self.other.refresh_from_db()
        self.assertEqual(self.other.config, {"judge_model": "kept", "judge_api_key": "kept-too"})

    def test_the_key_is_never_logged(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            self.migrate()
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        self.assertNotIn(KEY, stream.getvalue())

    def test_going_back_restores_nothing(self):
        self.migrate()
        with connection.schema_editor() as editor:
            MIGRATION.Migration.operations[0].reverse_code(django_apps, editor)
        self.saved.refresh_from_db()
        self.assertNotIn("judge_api_key", self.saved.config)
