"""Stored plugin configurations keep no plaintext secret (aisc_backend.secret_cleanup.drop_config_secrets, the
configuration step of `manage.py clean_old_secrets apply`).

Plugin A holds the narrow rule's configuration; plugin B a configuration with nothing secret, and a second row
that is not a dict.
"""
import copy
import importlib
import io
import json
import logging

from django.test import TestCase

from aisc_backend.models import Plugin, PluginConfig, Project
from aisc_backend.tests.test_config_secrets import STORED, STORED_AFTER

DROPPED_VALUES = ["abc123", "sk-proj-abcdefghijklmnopqrstuv", "hf_abcdefghijklmnopqrstu", "tok-1234567890abcdef"]


def cleanup():
    return importlib.import_module("aisc_backend.secret_cleanup")


class StoredConfigs(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", description="")
        a = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                  version="0.1.0")
        b = Plugin.objects.create(project=project, name="B", display_name="B", package_name="aisc-plugin-b",
                                  version="0.1.0")
        self.a = PluginConfig.objects.create(plugin=a, config=copy.deepcopy(STORED))
        self.b = PluginConfig.objects.create(plugin=b, config={"languages": ["en"], "max_tokens": 10})
        self.b2 = PluginConfig.objects.create(plugin=b, config=["not", "a", "dict"])

    def config(self, row):
        return PluginConfig.objects.get(pk=row.pk).config

    def test_count_lists_the_fields_it_would_drop_and_changes_nothing(self):
        found = cleanup().drop_config_secrets(PluginConfig, "default", apply=False)
        self.assertEqual(found, (1, [("aisc-plugin-a", "api_key"), ("aisc-plugin-a", "judge.openai_api_key"),
                                     ("aisc-plugin-a", "hf_token"), ("aisc-plugin-a", "targets[0].auth_token")]))
        self.assertEqual(self.config(self.a), STORED)

    def test_apply_drops_the_secrets_and_leaves_other_configurations(self):
        cleanup().drop_config_secrets(PluginConfig, "default", apply=True)
        self.assertEqual(self.config(self.a), STORED_AFTER)
        self.assertEqual(self.config(self.b), {"languages": ["en"], "max_tokens": 10})
        self.assertEqual(self.config(self.b2), ["not", "a", "dict"])

    def test_no_value_is_logged_and_a_second_apply_changes_nothing(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            cleanup().drop_config_secrets(PluginConfig, "default", apply=True)
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        for value in DROPPED_VALUES:
            self.assertNotIn(value, stream.getvalue())
        self.assertEqual(cleanup().drop_config_secrets(PluginConfig, "default", apply=True), (0, []))
        self.assertEqual(self.config(self.a), STORED_AFTER)
        self.assertNotIn("abc123", json.dumps(self.config(self.a)))
