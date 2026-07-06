"""
UNIT tests for aisc_backend.audit.log.log_action — NO live services (clerk mocked).

Asserts: (1) actor is extracted from the request's verified token (request.auth), defaulting to "unknown";
(2) source_ip is read from the request; (3) action/resource/metadata are passed through; (4) log_action
NEVER raises even if the clerk write fails (an audit failure must not break the user's action).
"""
import types
import unittest.mock as mock

from django.test import SimpleTestCase

from aisc_backend.audit import log as log_mod


def _req(auth, meta=None):
    return types.SimpleNamespace(auth=auth, META=meta or {})


class LogActionUnitTest(SimpleTestCase):
    def setUp(self):
        self.p = mock.patch.object(log_mod, "clerk")
        self.clerk = self.p.start()
        self.addCleanup(self.p.stop)

    def test_actor_and_fields_from_token(self):
        log_mod.log_action(_req({"preferred_username": "admin"}, {"REMOTE_ADDR": "1.2.3.4"}),
                           action="create", resource_type="project", resource_id="1", metadata={"name": "X"})
        kw = self.clerk.write_event.call_args.kwargs
        self.assertEqual(kw["actor"], "admin")
        self.assertEqual(kw["action"], "create")
        self.assertEqual(kw["resource_type"], "project")
        self.assertEqual(kw["resource_id"], "1")
        self.assertEqual(kw["metadata"], {"name": "X"})
        self.assertEqual(kw["source_app"], "backend")
        self.assertEqual(kw["source_ip"], "1.2.3.4")

    def test_source_ip_prefers_forwarded_for(self):
        log_mod.log_action(_req(True, {"HTTP_X_FORWARDED_FOR": "9.9.9.9, 10.0.0.1", "REMOTE_ADDR": "10.0.0.1"}),
                           action="run", resource_type="evaluation")
        self.assertEqual(self.clerk.write_event.call_args.kwargs["source_ip"], "9.9.9.9")

    def test_actor_unknown_when_auth_disabled_or_missing(self):
        log_mod.log_action(_req(True), action="x", resource_type="y")        # auth disabled (bypass)
        self.assertEqual(self.clerk.write_event.call_args.kwargs["actor"], "unknown")
        log_mod.log_action(_req(None), action="x", resource_type="y")        # no claims
        self.assertEqual(self.clerk.write_event.call_args.kwargs["actor"], "unknown")

    def test_never_raises_on_clerk_failure(self):
        self.clerk.write_event.side_effect = RuntimeError("immudb down")
        try:
            log_mod.log_action(_req({"preferred_username": "u"}), action="create", resource_type="project")
        except Exception as e:  # noqa: BLE001
            self.fail(f"log_action raised: {e}")
