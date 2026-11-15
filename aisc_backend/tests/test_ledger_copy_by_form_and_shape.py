"""Ledger copies of a plugin configuration hide what the plugin's form marks as secret, and any value shaped like
a key, whatever its name. A field is not hidden for its name alone: `tokens: 512` or `stop_token: "END"` stay
readable, so the ledger still says how a run was configured.

The module is imported in each test, so a missing function fails its own cases only.
"""
import importlib
import inspect

from django.test import SimpleTestCase

SHAPED = "sk-abcdefghijklmnopqrst1234"


def _rule():
    return importlib.import_module("aisc_backend.config_secrets")


def _masked(value, marked=None):
    rule = _rule()
    if marked is None:
        return rule.masked_for_ledger(value)
    if "secret_paths" not in inspect.signature(rule.masked_for_ledger).parameters:
        raise AssertionError("masked_for_ledger takes no secret_paths: the form's marks cannot reach it")
    return rule.masked_for_ledger(value, frozenset(marked))


class LedgerCopyByFormAndShape(SimpleTestCase):
    def test_token_counts_stay_visible(self):
        self.assertEqual(_masked({"tokens": 512, "max_tokens": 64}), {"tokens": 512, "max_tokens": 64})

    def test_a_field_the_form_marks_is_masked_whatever_its_name(self):
        self.assertEqual(_masked({"phrase": "open sesame"}, marked={"phrase"}), {"phrase": "set"})

    def test_an_unmarked_key_is_masked_by_its_shape_and_a_number_is_kept(self):
        self.assertEqual(_masked({"api_key": SHAPED, "max_tokens": 512}), {"api_key": "set", "max_tokens": 512})

    def test_a_short_word_under_a_token_name_is_kept(self):
        self.assertEqual(_masked({"stop_token": "END"}), {"stop_token": "END"})

    def test_numbers_and_booleans_are_never_masked(self):
        self.assertEqual(_masked({"secret_count": 3, "use_password_auth": True}),
                         {"secret_count": 3, "use_password_auth": True})

    def test_the_plugin_form_gives_its_secret_paths(self):
        plugin = importlib.import_module("aisc_backend.routers.plugin")
        self.assertTrue(hasattr(plugin, "form_secret_paths"),
                        "routers.plugin has no form_secret_paths: the ledger copy cannot follow the form's marks")
        schema = {"properties": {"phrase": {"type": "string", "format": "password"},
                                 "tokens": {"type": "integer"},
                                 "judge": {"type": "object", "properties": {"pin": {"type": "string",
                                                                                     "writeOnly": True}}}}}
        self.assertEqual(plugin.form_secret_paths(schema, {}), frozenset({"phrase", "judge.pin"}))
