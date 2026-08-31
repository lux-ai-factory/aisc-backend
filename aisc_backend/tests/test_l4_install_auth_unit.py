"""L4 — Level 1 unit tests: the static-token install gate in isolation.

Tests the pure helpers ``_token_ok`` / ``_bearer_token`` (no HTTP, no DB): the
token gate denies by default, accepts only the exact token, never raises on a
non-ASCII token (compared over raw UTF-8 bytes), and the header parser only
accepts a well-formed ``Bearer <token>``.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from aisc_backend.routers.catalogue_install import _bearer_token, _token_ok


def _req(authorization=None):
    headers = {} if authorization is None else {"Authorization": authorization}
    return SimpleNamespace(headers=headers)


class TokenOkUnitTests(SimpleTestCase):
    @override_settings(CATALOGUE_INSTALL_TOKEN="")
    def test_rejects_when_unset(self):
        # Empty configured token => never grant access (deny by default).
        self.assertFalse(_token_ok("anything"))

    @override_settings(CATALOGUE_INSTALL_TOKEN="s3cr3t")
    def test_accepts_correct(self):
        self.assertTrue(_token_ok("s3cr3t"))

    @override_settings(CATALOGUE_INSTALL_TOKEN="s3cr3t")
    def test_rejects_wrong(self):
        self.assertFalse(_token_ok("wrong"))

    @override_settings(CATALOGUE_INSTALL_TOKEN="s3cr3t")
    def test_rejects_missing(self):
        self.assertFalse(_token_ok(None))

    @override_settings(CATALOGUE_INSTALL_TOKEN="clé-secrète-😀")
    def test_constant_time_non_ascii(self):
        # Non-ASCII token compares over bytes: wrong refuses cleanly (no exception),
        # exact still matches.
        self.assertFalse(_token_ok("wrong-ascii"))
        self.assertTrue(_token_ok("clé-secrète-😀"))


class BearerParserUnitTests(SimpleTestCase):
    def test_extracts_bearer(self):
        self.assertEqual(_bearer_token(_req("Bearer abc")), "abc")

    def test_missing_header(self):
        self.assertIsNone(_bearer_token(_req(None)))

    def test_wrong_scheme(self):
        self.assertIsNone(_bearer_token(_req("Basic abc")))

    def test_empty_token(self):
        self.assertIsNone(_bearer_token(_req("Bearer ")))
