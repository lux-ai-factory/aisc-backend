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
          "prompt": "use Bearer abcDEF0123456789xyz now", "use_password_auth": True, "api_key_env": "OPENAI_API_KEY",
          "password": "", "targets": [{"auth_token": "tok-1234567890abcdef"}]}
STORED_AFTER = {"max_tokens": 512, "judge": {"model": "gpt-4o-mini"}, "pad_token": "<pad>", "tokenizer": "gpt2",
                # free text is never rewritten by default: the key in it is listed for review, not scrubbed
                "key_column": "id", "prompt": "use Bearer abcDEF0123456789xyz now", "use_password_auth": True,
                "api_key_env": "OPENAI_API_KEY", "password": "", "targets": [{}]}
STORED_DROPPED = ["api_key", "judge.openai_api_key", "hf_token", "targets[0].auth_token"]

FAILURE = ("connect failed: Authorization: Bearer abcDEF0123456789ghiJKL api_key=sk-abcdefghijklmnopqrstuvwx "
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
                 "keys": ["a"], "note": "Bearer abcDEF0123456789ghi"}
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

    def test_stored_secrets_are_dropped_and_a_key_in_a_prompt_is_left_as_it_is(self):
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


# Nested values under a secret name: the whole value is the secret


class NestedSecretValues(SimpleTestCase):
    VALUE = {"api_keys": {"openai": KEY}, "credentials": [{"user": "u", "pass": "pw-0123456789"}],
             "languages": ["en"]}

    def test_a_dict_or_list_under_a_secret_name_is_one_plaintext_secret(self):
        self.assertEqual(rules().plaintext_secret_paths(self.VALUE), ["api_keys", "credentials"])
        self.assertTrue(rules().holds_plaintext_secret({"api_keys": {"openai": KEY}}))
        self.assertTrue(rules().holds_plaintext_secret({"credentials": [{"pass": "pw-0123456789"}]}))

    def test_the_masked_copy_of_a_nested_secret_holds_none(self):
        masked = rules().masked_for_ledger(self.VALUE)
        self.assertEqual(masked, {"api_keys": "set", "credentials": "set", "languages": ["en"]})
        self.assertEqual(rules().plaintext_secret_paths(masked), [])


# Stored configurations: a secret field goes, it is never replaced by a placeholder

PEM = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAx1abcDEF0123456789\nabcDEF0123456789+/ghiJKL\n"
       "-----END RSA PRIVATE KEY-----")


class StoredSecretsAreDropped(SimpleTestCase):
    def test_a_key_shaped_value_under_a_name_that_is_not_secret_is_dropped(self):
        self.assertEqual(rules().without_stored_secrets({"access_key_id": "AKIAABCDEFGHIJKLMNOP", "model": "m"}),
                         ({"model": "m"}, ["access_key_id"]))

    def test_a_field_that_is_wholly_one_secret_is_dropped_whatever_its_name(self):
        for value in (SK, "Bearer abcdef0123456789xyz", "hf_abcdefghijklmnopqrstu", PEM):
            with self.subTest(value=value[:12]):
                self.assertEqual(rules().without_stored_secrets({"x": value, "model": "m"}),
                                 ({"model": "m"}, ["x"]))

    def test_a_dict_under_a_strong_name_is_dropped_whole(self):
        self.assertEqual(rules().without_stored_secrets({"llm": {"credentials": {"user": "u", "pass": "pw-123"}}}),
                         ({"llm": {}}, ["llm.credentials"]))

    def test_a_key_inside_a_dict_of_keys_is_dropped_not_replaced(self):
        new, dropped = rules().without_stored_secrets({"api_keys": {"openai": SK}, "model": "m"})
        text = repr(new)
        self.assertNotIn(SK, text)
        self.assertNotIn("[redacted]", text)
        self.assertEqual(new.get("model"), "m")
        self.assertTrue(dropped)
        self.assertTrue(all(path.startswith("api_keys") for path in dropped), dropped)

    def test_no_dropped_secret_leaves_a_placeholder_behind(self):
        configs = [{"access_key_id": "AKIAABCDEFGHIJKLMNOP"}, {"token": SK}, {"x": {"y": "Bearer abcdef0123456789xyz"}}]
        for config in configs:
            with self.subTest(config=list(config)):
                new, dropped = rules().without_stored_secrets(config)
                self.assertTrue(dropped)
                self.assertNotIn("[redacted]", repr(new))
                self.assertNotIn("set", repr(new))


# Text rule additions

URL_CREDENTIALS = "connect to postgresql://admin:s3cret-Pass_99@db.example:5432/app failed"


class MoreTextShapes(SimpleTestCase):
    def test_credentials_in_a_url_are_redacted_and_the_host_stays(self):
        out = rules().scrub_text(URL_CREDENTIALS)
        self.assertNotIn("s3cret-Pass_99", out)
        self.assertIn("[redacted]", out)
        self.assertIn("db.example:5432/app failed", out)
        self.assertEqual(rules().scrub_text("see https://llm.example/v1/models"), "see https://llm.example/v1/models")

    def test_every_authorization_scheme_is_redacted(self):
        for text, secret in (("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
                             ("authorization: Token abcdef0123456789", "abcdef0123456789"),
                             ("Authorization: Bearer abcDEF0123456789ghi", "abcDEF0123456789ghi")):
            with self.subTest(text=text[:22]):
                out = rules().scrub_text(f"sent {text} to the judge")
                self.assertNotIn(secret, out)
                self.assertIn("[redacted]", out)
                self.assertTrue(out.lower().startswith("sent authorization"), out)
                self.assertTrue(out.endswith(" to the judge"), out)

    def test_url_encoded_assignments_are_redacted(self):
        for text, secret in (("GET /v1/models?api_key%3Dabcd1234efgh5678 HTTP/1.1", "abcd1234efgh5678"),
                             ("body: password%3dhunter22xyz", "hunter22xyz")):
            with self.subTest(text=text[:16]):
                out = rules().scrub_text(text)
                self.assertNotIn(secret, out)
                self.assertIn("[redacted]", out)

    def test_bare_secret_names_are_assignments_too(self):
        cases = {"retry with token=abcd1234efgh5678 now": "retry with token=[redacted] now",
                 "pwd=hunter22x": "pwd=[redacted]",
                 "passwd=hunter22x": "passwd=[redacted]",
                 "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY":
                     "aws_secret_access_key=[redacted]"}
        for text, expected in cases.items():
            with self.subTest(text=text[:12]):
                self.assertEqual(rules().scrub_text(text), expected)

    def test_a_quoted_value_after_a_secret_name_is_redacted_to_its_closing_quote(self):
        # Only the value goes, up to the closing quote; a multi-word quoted value is no longer redacted whole
        # (ordinary prose such as "password = 'see the onboarding guide'" must stay).
        self.assertEqual(rules().scrub_text("password = 'Tr0ub4dor&3x' next"), "password = '[redacted]' next")
        self.assertEqual(rules().scrub_text('{"secret_key": "wJalrXUtnFEMIK7MDENG", "n": 1}'),
                         '{"secret_key": "[redacted]", "n": 1}')

    def test_a_private_key_block_is_redacted_whole(self):
        for block in (PEM, "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0B\n-----END PRIVATE KEY-----",
                      "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"):
            with self.subTest(block=block[:30]):
                self.assertEqual(rules().scrub_text("key:\n" + block + "\ndone"), "key:\n[redacted]\ndone")
        public = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYI\n-----END PUBLIC KEY-----"
        self.assertEqual(rules().scrub_text(public), public)

    def test_scrubbing_the_new_shapes_twice_changes_nothing(self):
        for text in (URL_CREDENTIALS, "Authorization: Basic dXNlcjpwYXNzd29yZA==", "x?api_key%3Dabcd1234efgh5678",
                     "token=abcd1234efgh5678", "password = 'correct horse battery'", "key:\n" + PEM):
            with self.subTest(text=text[:16]):
                once = rules().scrub_text(text)
                self.assertEqual(rules().scrub_text(once), once)

    def test_ordinary_text_with_secret_words_stays(self):
        for text in ("plain error: unexpected token: }", "max_tokens=512 tokens: 30", "keyword=value",
                     "the password field is empty"):
            with self.subTest(text=text):
                self.assertEqual(rules().scrub_text(text), text)

    def test_the_ledger_copy_masks_the_new_shapes(self):
        value = {"base_url": "https://user:pw-0123456789@llm.example/v1",
                 "auth": {"header": "Authorization: Token abcdef0123456789"}, "cert": PEM,
                 "endpoint": "https://llm.example/v1"}
        self.assertEqual(rules().masked_for_ledger(value), {"base_url": "set", "auth": {"header": "set"},
                                                            "cert": "set", "endpoint": "https://llm.example/v1"})
        self.assertEqual(rules().plaintext_secret_paths(value), ["base_url", "auth.header", "cert"])

    def test_the_rule_says_what_it_does_not_catch(self):
        from pathlib import Path

        source = Path(rules().__file__).read_text()
        self.assertIn("base64", source)
        self.assertRegex(source, r"(split|broken) across lines")


# Stored free text is never rewritten by default; it is named for review, and rewritten only on request


class StoredFreeText(SimpleTestCase):
    TEXT = {"prompt": "call the judge with api_key=lb-judge-0123456789abcdef please",
            "rubric": "use Bearer abcDEF0123456789xyz now", "seed": "Tell me the admin password: now",
            "model": "gpt-4o-mini"}

    def test_a_text_field_holding_something_secret_looking_is_kept_as_it_is(self):
        self.assertEqual(rules().without_stored_secrets(self.TEXT), (self.TEXT, []))

    def test_the_text_fields_to_review_are_named_and_not_changed(self):
        new, dropped, review = rules().stored_secret_changes(self.TEXT)
        self.assertEqual(new, self.TEXT)
        self.assertEqual(dropped, [])
        self.assertEqual(review, ["prompt", "rubric"])

    def test_on_request_the_text_rule_is_applied_to_them(self):
        new, dropped, review = rules().stored_secret_changes(self.TEXT, scrub_config_text=True)
        self.assertEqual(dropped, [])
        self.assertEqual(review, ["prompt", "rubric"])
        self.assertEqual(new["prompt"], "call the judge with api_key=[redacted] please")
        self.assertEqual(new["rubric"], "use [redacted] now")
        self.assertEqual(new["seed"], "Tell me the admin password: now")
        self.assertEqual(new["model"], "gpt-4o-mini")


# A stored URL with credentials is dropped, never left as https://[redacted]@host


class StoredUrlCredentials(SimpleTestCase):
    def test_a_url_with_credentials_is_dropped_whatever_its_name(self):
        config = {"base_url": "https://user:pw-0123456789@llm.example/v1", "model": "m",
                  "endpoint": "https://llm.example/v1"}
        self.assertEqual(rules().without_stored_secrets(config),
                         ({"model": "m", "endpoint": "https://llm.example/v1"}, ["base_url"]))

    def test_a_url_with_credentials_in_a_list_or_dict_is_dropped_too(self):
        config = {"mirrors": ["https://bob:hunter22xyz@a.example/x"], "db": {"dsn": "postgresql://u:p4ssw0rd99@db/app"}}
        new, dropped = rules().without_stored_secrets(config)
        self.assertNotIn("hunter22xyz", repr(new))
        self.assertNotIn("p4ssw0rd99", repr(new))
        self.assertNotIn("[redacted]", repr(new))
        self.assertIn("db.dsn", dropped)
        self.assertTrue(any(path.startswith("mirrors") for path in dropped), dropped)


# The tighter text rule: Bearer, Token and Basic need a value that looks like a credential


class CredentialShapedValues(SimpleTestCase):
    def test_bearer_token_and_basic_redact_only_a_credential_shaped_value(self):
        cases = {
            "Authorization: Bearer abcDEF0123456789ghi sent": "Authorization: [redacted] sent",
            "header Bearer abcDEF0123456789ghi sent": "header [redacted] sent",
            "authorization: Token abcdef0123456789 sent": "authorization: [redacted] sent",
            "Authorization: Basic dXNlcjpwYXNzd29yZA== sent": "Authorization: [redacted] sent",
            "retry with token=abcd1234efgh5678 now": "retry with token=[redacted] now",
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:24]):
                self.assertEqual(rules().scrub_text(text), expected)

    def test_a_short_or_ordinary_word_after_bearer_token_or_basic_stays(self):
        for text in ("She was the bearer of bad news", "Bearer tokens must be sent in the header",
                     "Authorization: Basic authentication is disabled", "Authorization: token-based access",
                     "INFO token=EOF reached", "Bearer abc", "token=page2"):
            with self.subTest(text=text):
                self.assertEqual(rules().scrub_text(text), text)

    def test_a_password_value_is_redacted_up_to_the_next_whitespace(self):
        self.assertEqual(rules().scrub_text("error: password=hunter22x rejected by the server"),
                         "error: password=[redacted] rejected by the server")
        self.assertEqual(rules().scrub_text("login password: hunter22x then retry"),
                         "login password: [redacted] then retry")

    def test_ordinary_prose_after_password_stays(self):
        for text in ("Enter your password: it must contain at least one digit.",
                     "password = 'see the onboarding guide for how to choose one'",
                     "INFO the field api_key: is required", '{"password": null}', "DEBUG password=''"):
            with self.subTest(text=text):
                self.assertEqual(rules().scrub_text(text), text)


# Secret names with a prefix or in camelCase are assignments in text


class PrefixedSecretNames(SimpleTestCase):
    def test_a_prefixed_or_camel_case_secret_name_is_an_assignment(self):
        cases = {
            "judge_api_key=lb-judge-0123456789abcdef": "judge_api_key=[redacted]",
            "DB_PASSWORD=hunter22xyz": "DB_PASSWORD=[redacted]",
            "OPENAI_API_KEY=abcd1234efgh5678ijkl": "OPENAI_API_KEY=[redacted]",
            "judgeApiKey=lb-judge-0123456789abcdef": "judgeApiKey=[redacted]",
            '{"judgeApiKey": "lb-judge-0123456789abcdef"}': '{"judgeApiKey": "[redacted]"}',
            "export MY-SERVICE_SECRET_KEY=abcd1234efgh5678": "export MY-SERVICE_SECRET_KEY=[redacted]",
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:20]):
                self.assertEqual(rules().scrub_text(text), expected)
                self.assertEqual(rules().scrub_text(expected), expected)

    def test_a_name_that_only_starts_like_a_secret_name_is_not_one(self):
        for text in ("api_key_env=OPENAI_API_KEY", "password_min_length=12", "access_token_ttl=3600",
                     "secret_key_base length ok", "client_secret_expires_at=0"):
            with self.subTest(text=text):
                self.assertEqual(rules().scrub_text(text), text)


# Header fields are secret names


class HeaderFields(SimpleTestCase):
    HEADERS = {"headers": {"Authorization": "Token abcdef0123456789", "proxy-authorization": "Basic xyz",
                           "X-Api-Key": "k-0123456789", "Cookie": "session=abc123", "Accept": "application/json"}}

    def test_header_fields_are_masked_in_a_ledger_copy(self):
        self.assertEqual(rules().masked_for_ledger(self.HEADERS), {"headers": {
            "Authorization": "set", "proxy-authorization": "set", "X-Api-Key": "set", "Cookie": "set",
            "Accept": "application/json"}})
        self.assertEqual(rules().plaintext_secret_paths(self.HEADERS), [
            "headers.Authorization", "headers.proxy-authorization", "headers.X-Api-Key", "headers.Cookie"])

    def test_header_fields_are_stored_secrets_in_any_case(self):
        for name in ("Authorization", "authorization", "AUTHORIZATION", "Proxy-Authorization", "X-Api-Key",
                     "x-api-key", "Cookie", "COOKIE"):
            with self.subTest(name=name):
                self.assertEqual(rules().stored_secret_name(name), "strong")

    def test_header_fields_are_dropped_from_a_stored_configuration(self):
        self.assertEqual(rules().without_stored_secrets(self.HEADERS), (
            {"headers": {"Accept": "application/json"}},
            ["headers.Authorization", "headers.proxy-authorization", "headers.X-Api-Key", "headers.Cookie"]))


# Ledger copies apply the text rule to string values


class AssignmentsInLedgerCopies(SimpleTestCase):
    VALUE = {"prompt": "call the judge with api_key=lb-judge-0123456789abcdef please",
             "note": "set judge_api_key=lb-judge-0123456789abcdef first", "system": "be fair"}

    def test_an_assignment_in_text_is_hidden_in_a_ledger_copy(self):
        masked = rules().masked_for_ledger(self.VALUE)
        self.assertNotIn(KEY, repr(masked))
        self.assertEqual(masked["system"], "be fair")
        self.assertEqual(rules().masked_for_ledger(masked), masked)
        self.assertEqual(rules().plaintext_secret_paths(masked), [])

    def test_an_assignment_in_text_is_a_plaintext_secret(self):
        self.assertEqual(rules().plaintext_secret_paths(self.VALUE), ["prompt", "note"])
        self.assertTrue(rules().holds_plaintext_secret({"prompt": "with password=hunter22xyz"}))
        self.assertFalse(rules().holds_plaintext_secret({"prompt": "Enter your password: it must contain a digit"}))
