"""
UNIT tests for aisc_backend.audit.log.log_action — NO live services (clerk mocked).

Asserts: (1) "who" is extracted from the request's verified token (request.auth), defaulting to "unknown";
(2) body/consequence is passed through; (3) log_action NEVER raises even if the clerk write fails
(an audit failure must not break the user's action).
"""
import types
import unittest.mock as mock

from django.test import SimpleTestCase

from aisc_backend.audit import log as log_mod


def _req(auth):
    return types.SimpleNamespace(auth=auth)


class LogActionUnitTest(SimpleTestCase):
    def setUp(self):
        self.p = mock.patch.object(log_mod, "clerk")
        self.clerk = self.p.start()
        self.addCleanup(self.p.stop)

    def test_who_from_token_claims(self):
        log_mod.log_action(_req({"preferred_username": "admin"}), "project:create", {"pid": "1"})
        kw = self.clerk.write_event.call_args.kwargs
        self.assertEqual(kw["who"], "admin")
        self.assertEqual(kw["what"], "project:create")
        self.assertEqual(kw["consequence"], {"pid": "1"})
        self.assertEqual(kw["app"], "backend")

    def test_who_unknown_when_auth_disabled_or_missing(self):
        log_mod.log_action(_req(True), "x:y")                 # auth disabled (bypass)
        self.assertEqual(self.clerk.write_event.call_args.kwargs["who"], "unknown")
        log_mod.log_action(_req(None), "x:y")                 # no claims
        self.assertEqual(self.clerk.write_event.call_args.kwargs["who"], "unknown")

    def test_never_raises_on_clerk_failure(self):
        self.clerk.write_event.side_effect = RuntimeError("immudb down")
        # must NOT raise — audit failure cannot break the action
        try:
            log_mod.log_action(_req({"preferred_username": "u"}), "project:create", {"pid": "1"})
        except Exception as e:  # noqa: BLE001
            self.fail(f"log_action raised: {e}")
