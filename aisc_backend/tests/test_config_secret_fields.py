"""Which fields of a saved plugin configuration are keys (aisc_backend.config_secret_fields).

A key field is taken out of the configuration to store and kept as a project secret instead; the rule says which
fields those are, refuses the configurations it cannot keep that way, and gives the form its hints.
"""
import copy
import importlib

from django.test import SimpleTestCase

from aisc_backend.tests.test_ledger_events import _PluginWithSecrets


def rule():
    return importlib.import_module("aisc_backend.config_secret_fields")


def split(config, schema=None, ui=None):
    return rule().split_secret_fields(config, schema or {}, ui or {})


class SplittingAConfiguration(SimpleTestCase):
    def assertSplitLeavesItsInput(self, config, schema=None, ui=None):
        before = copy.deepcopy(config)
        result = split(config, schema, ui)
        self.assertEqual(config, before)
        return result

    def test_the_name_and_value_rule_alone_moves_only_the_keys(self):
        config = {"threshold": 0.5, "evaluator_llm_api_key": "sk-judge-0123456789abcdef", "embeddings_api_key": "",
                  "model_credential": "cred-value-1", "promptfoo_email": "a@b.c", "tokens": 60, "max_tokens": 512,
                  "hf_token": "short", "pad_token": "<pad>", "note": "hello"}
        result = self.assertSplitLeavesItsInput(config)
        self.assertEqual(result, rule().SplitConfig(
            kept={"threshold": 0.5, "embeddings_api_key": "", "promptfoo_email": "a@b.c", "tokens": 60,
                  "max_tokens": 512, "hf_token": "short", "pad_token": "<pad>", "note": "hello"},
            moved={"evaluator_llm_api_key": "sk-judge-0123456789abcdef", "model_credential": "cred-value-1"},
            not_text=[]))

    def test_the_forms_marks_move_a_field_whatever_its_name(self):
        config = {"threshold": 0.5, "passphrase": "pass-value-1", "sealed": "sealed-value-2",
                  "auth_header": "header-value-3", "judge": {"model": "gpt-4o", "phrase": "phrase-value-4"},
                  "access_token": ""}
        schema, ui = _PluginWithSecrets().get_full_schema()
        result = self.assertSplitLeavesItsInput(config, schema, ui)
        self.assertEqual(result.kept, {"threshold": 0.5, "judge": {"model": "gpt-4o"}, "access_token": ""})
        self.assertEqual(list(result.moved), ["passphrase", "sealed", "auth_header", "judge.phrase"])
        self.assertEqual(result.moved, {"passphrase": "pass-value-1", "sealed": "sealed-value-2",
                                        "auth_header": "header-value-3", "judge.phrase": "phrase-value-4"})

        unmarked = self.assertSplitLeavesItsInput(config)
        self.assertEqual(unmarked.kept["auth_header"], "header-value-3")
        self.assertEqual(unmarked.kept["sealed"], "sealed-value-2")
        self.assertEqual(unmarked.kept["judge"], {"model": "gpt-4o", "phrase": "phrase-value-4"})
        self.assertEqual(unmarked.moved, {"passphrase": "pass-value-1"})

    def test_a_value_shaped_like_a_key_moves_whatever_its_name_and_free_text_stays(self):
        config = {"endpoint": "https://user:pw-123@host/v1", "note": "sk-abcdefghijklmnopqrstuv",
                  "prompt": "use api_key=sk-abc in text", "keys": ["sk-abcdefghijklmnopqrstuv", "plain"]}
        result = self.assertSplitLeavesItsInput(config)
        self.assertEqual(result.kept, {"prompt": "use api_key=sk-abc in text", "keys": ["", "plain"]})
        self.assertEqual(list(result.moved), ["endpoint", "note", "keys[0]"])
        self.assertEqual(result.moved, {"endpoint": "https://user:pw-123@host/v1",
                                        "note": "sk-abcdefghijklmnopqrstuv",
                                        "keys[0]": "sk-abcdefghijklmnopqrstuv"})

    def test_a_list_or_object_under_a_key_name_is_not_text_and_a_name_that_says_nothing_is_walked(self):
        config = {"api_key": {"openai": "sk-abcdefghijklmnopqrstuv"}, "credentials": ["a"],
                  "hf_tokens": ["abcdefghijklmnopqrstu"], "empty_keys": {}}
        result = self.assertSplitLeavesItsInput(config)
        self.assertEqual(result.not_text, ["api_key", "credentials"])
        self.assertEqual(result.kept["hf_tokens"], ["abcdefghijklmnopqrstu"])

        walked = self.assertSplitLeavesItsInput({"api_keys": {"openai": "sk-abcdefghijklmnopqrstuv"}})
        self.assertEqual(walked.moved, {"api_keys.openai": "sk-abcdefghijklmnopqrstuv"})


class SecretEnvironmentName(SimpleTestCase):
    def test_a_path_becomes_the_variable_the_worker_sets(self):
        self.assertEqual(rule().secret_env_name("model_credential"), "AISC_SECRET_CONFIG_MODEL_CREDENTIAL")
        self.assertEqual(rule().secret_env_name("judge.phrase"), "AISC_SECRET_CONFIG_JUDGE_PHRASE")
        self.assertEqual(rule().secret_env_name("targets[0].auth_token"), "AISC_SECRET_CONFIG_TARGETS_0__AUTH_TOKEN")


class CheckingASplit(SimpleTestCase):
    def assertRefused(self, split_config, adopted, message):
        with self.assertRaises(rule().SecretFieldRefused) as raised:
            rule().check_split(split_config, adopted=adopted)
        self.assertEqual(str(raised.exception), message)

    def test_each_refusal_says_why(self):
        r = rule()
        self.assertRefused(r.SplitConfig(kept={}, moved={"target_api_key": "x"}, not_text=[]), True,
                           r.ADOPTED_REFUSAL.format(fields="target_api_key"))
        self.assertRefused(r.SplitConfig(kept={}, moved={}, not_text=["api_key"]), False,
                           r.NOT_TEXT.format(fields="api_key"))

        dotted = split({"x.api_key": "v-0123456789abcdef"})
        self.assertEqual(dotted.moved, {"x.api_key": "v-0123456789abcdef"})
        self.assertRefused(dotted, False, r.BAD_NAME.format(path="x.api_key"))

        same = split({"a": {"b_api_key": "v1-0123456789abcdef"}, "a_b": {"api_key": "v2-0123456789abcdef"}})
        self.assertRefused(same, False, r.SAME_NAME.format(first="a.b_api_key", second="a_b.api_key"))

        seventeen = split({f"k{n:02d}_api_key": f"value-{n:02d}-0123456789" for n in range(1, 18)})
        self.assertRefused(seventeen, False, r.TOO_MANY.format(count=17))
        sixteen = split({f"k{n:02d}_api_key": f"value-{n:02d}-0123456789" for n in range(1, 17)})
        self.assertIsNone(r.check_split(sixteen, adopted=False))


class FormHints(SimpleTestCase):
    def test_a_kept_field_is_a_password_input_with_its_secret_named(self):
        r = rule()
        ui = {"judge": {"ui:order": ["model"]}, "auth_header": {"ui:widget": "password"},
              "x": {"ui:widget": "hidden"}}
        before = copy.deepcopy(ui)
        hinted = r.secret_hints(ui, {"judge.phrase": "k1", "auth_header": "k2", "x": "k3",
                                     "targets[0].token": "k4"})
        self.assertEqual(hinted, {
            "judge": {"ui:order": ["model"],
                      "phrase": {"ui:widget": "password", "ui:help": r.SECRET_HELP.format(key="k1")}},
            "auth_header": {"ui:widget": "password", "ui:help": r.SECRET_HELP.format(key="k2")},
            "x": {"ui:widget": "hidden", "ui:help": r.SECRET_HELP.format(key="k3")}})
        self.assertEqual(ui, before)
