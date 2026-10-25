"""The secret rules (aisc_backend.config_secrets): the broad one for ledger copies, the narrow one for stored
configurations, and the text scrubber for failure messages.

The module is imported in each test, so a missing function fails its own cases only.
"""
import importlib

from django.test import SimpleTestCase

KEY = "lb-judge-0123456789abcdef"
SK = "sk-proj-abcdefghijklmnopqrstuv"

#: The stored configuration of the narrow rule's cases.
STORED = {"api_key": "abc123", "max_tokens": 512, "judge": {"openai_api_key": SK, "model": "gpt-4o-mini"},
          "pad_token": "<pad>", "hf_token": "hf_abcdefghijklmnopqrstu", "tokenizer": "gpt2", "key_column": "id",
          "prompt": "use Bearer xyz123 now", "use_password_auth": True, "api_key_env": "OPENAI_API_KEY",
          "password": "", "targets": [{"auth_token": "tok-1234567890abcdef"}]}
STORED_AFTER = {"max_tokens": 512, "judge": {"model": "gpt-4o-mini"}, "pad_token": "<pad>", "tokenizer": "gpt2",
                "key_column": "id", "prompt": "use [redacted] now", "use_password_auth": True,
                "api_key_env": "OPENAI_API_KEY", "password": "", "targets": [{}]}
STORED_DROPPED = ["api_key", "judge.openai_api_key", "hf_token", "targets[0].auth_token"]

FAILURE = ("connect failed: Authorization: Bearer abc.def.ghi api_key=sk-abcdefghijklmnopqrstuvwx "
           "password: 'hunter22' token-xyz MYSECRETVALUE123")
FAILURE_KNOWN = ["MYSECRETVALUE123", "short"]
FAILURE_SCRUBBED = ("connect failed: Authorization: [redacted] api_key=[redacted] password: '[redacted]' "
                    "token-xyz [redacted]")
SHAPES = "hf_abcdefghijklmnopqrstuvwx ghp_abcdefghijklmnopqrstuvwxyz AKIAABCDEFGHIJKLMNOP xoxb-1234567890-abc AIza" + "A" * 35


def rules():
    return importlib.import_module("aisc_backend.config_secrets")


class LedgerCopies(SimpleTestCase):
    VALUE = {"api_key": "abc123", "max_tokens": 512,
             "nested": {"auth_token": "", "items": [{"password": None}]},
             "prompt": "use sk-abcdefghijklmnopqrst1234 ok", "note": "x"}
    MASKED = {"api_key": "set", "max_tokens": "set", "nested": {"auth_token": None, "items": [{"password": None}]},
              "prompt": "set", "note": "x"}

    def test_secret_named_keys_are_set_or_none_and_a_secret_shaped_string_is_set(self):
        self.assertEqual(rules().masked_for_ledger(self.VALUE), self.MASKED)

    def test_masking_twice_changes_nothing(self):
        once = rules().masked_for_ledger(self.VALUE)
        self.assertEqual(rules().masked_for_ledger(once), once)

    def test_without_shapes_it_is_what_the_router_writes_for_a_plugin_it_cannot_load(self):
        from aisc_backend.routers import plugin

        value = {"judge_api_key": "k", "languages": ["en"], "x": {"secret": "s"}}
        expected = {"judge_api_key": "set", "languages": ["en"], "x": {"secret": "set"}}
        self.assertEqual(rules().masked_for_ledger(value), expected)
        self.assertEqual(plugin._masked(value, {}, {}, {}), expected)

    def test_the_router_uses_the_shared_rule(self):
        from aisc_backend.routers import plugin

        self.assertIs(plugin.SECRET_SET, rules().SECRET_SET)
        self.assertEqual(plugin._SECRET_NAME.pattern, rules().LEDGER_SECRET_NAME.pattern)
        self.assertEqual(plugin._SECRET_NAME.flags, rules().LEDGER_SECRET_NAME.flags)

    def test_the_paths_holding_a_plaintext_secret(self):
        value = {"api_key": "set", "judge_api_key": KEY, "max_tokens": 512, "list": [{"token": "abcdef"}],
                 "keys": ["a"], "note": "Bearer abc"}
        self.assertEqual(rules().plaintext_secret_paths(value), ["judge_api_key", "list[0].token", "keys", "note"])
        self.assertTrue(rules().holds_plaintext_secret(value))
        self.assertFalse(rules().holds_plaintext_secret({"api_key": "set", "max_tokens": 512, "password": None}))


class StoredConfigs(SimpleTestCase):
    def test_which_names_are_stored_secrets(self):
        expected = {"api_key": "strong", "judgeApiKey": "strong", "client_secret": "strong", "db_password": "strong",
                    "access_token": "strong", "secret_key": "strong", "aws_secret_access_key": "strong",
                    "hf_token": "weak", "openai_key": "weak", "pad_token": "weak", "max_tokens": None,
                    "tokenizer": None, "key_column": None, "keywords": None, "api_key_env": None,
                    "credentials_file": None, "password_hash": None}
        self.assertEqual({name: rules().stored_secret_name(name) for name in expected}, expected)

    def test_name_words(self):
        self.assertEqual(rules().name_words("judgeApiKey"), ["judge", "api", "key"])
        self.assertEqual(rules().name_words("hf_token"), ["hf", "token"])

    def test_stored_secrets_are_dropped_and_a_key_in_a_prompt_is_scrubbed(self):
        self.assertEqual(rules().without_stored_secrets(STORED), (STORED_AFTER, STORED_DROPPED))

    def test_dropping_twice_changes_nothing_and_a_config_that_is_not_a_dict_stays(self):
        self.assertEqual(rules().without_stored_secrets(STORED_AFTER), (STORED_AFTER, []))
        self.assertEqual(rules().without_stored_secrets(["a", "b"]), (["a", "b"], []))
        self.assertEqual(rules().without_stored_secrets(None), (None, []))


class FailureText(SimpleTestCase):
    def test_known_values_shapes_and_assignments_are_redacted(self):
        self.assertEqual(rules().scrub_text(FAILURE, known=FAILURE_KNOWN), FAILURE_SCRUBBED)

    def test_scrubbing_twice_changes_nothing(self):
        self.assertEqual(rules().scrub_text(FAILURE_SCRUBBED, known=FAILURE_KNOWN), FAILURE_SCRUBBED)

    def test_every_known_key_shape_is_redacted_and_plain_text_stays(self):
        self.assertEqual(rules().scrub_text(SHAPES), "[redacted] [redacted] [redacted] [redacted] [redacted]")
        self.assertEqual(rules().scrub_text(""), "")
        self.assertEqual(rules().scrub_text("plain error: unexpected token: }"), "plain error: unexpected token: }")
