"""
UNIT tests for the /audit endpoint (aisc_backend.routers.audit) — NO live services.

ninja TestClient(audit_router) with clerk.write_event mocked. AUTH_ENABLED is False in tests (dev bypass),
but ninja's HttpBearer still requires an Authorization header to be PRESENT (it 401s on a missing header
before the bypass runs) — real callers always forward the user's token, so we send a dummy bearer here.
With AUTH_ENABLED False the token isn't verified and request.auth becomes True -> "who" = "unknown".

We assert the BODY maps to the clerk call and that identity is NEVER taken from the body.

Run:  uv run manage.py test aisc_backend.tests.immudb.test_audit_endpoint_unit
"""
import unittest.mock as mock

from django.test import SimpleTestCase
from ninja.testing import TestClient

from aisc_backend.routers.audit import router as audit_router, AuditEventIn
from aisc_backend.auth import keycloak as kc_auth

AUTH = {"Authorization": "Bearer dummy"}   # header must be present; not verified when AUTH_ENABLED=False


class AuditEndpointUnitTest(SimpleTestCase):
    def setUp(self):
        # Pin AUTH_ENABLED False so the dummy token bypasses verification REGARDLESS of the ambient
        # env (the full regression run sets AUTH_ENABLED=true) -> deterministic unit test.
        self.ap = mock.patch.object(kc_auth, 'AUTH_ENABLED', False)
        self.ap.start(); self.addCleanup(self.ap.stop)
        self.client = TestClient(audit_router)
        self.p = mock.patch("aisc_backend.routers.audit.clerk")
        self.mock_clerk = self.p.start()
        self.addCleanup(self.p.stop)

    def test_post_audit_delegates_body_to_clerk(self):
        resp = self.client.post("", json={
            "what": "checklist:answer", "app": "controls",
            "consequence": {"submission": 45, "q": "Q3"}, "status": "ok",
        }, headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.mock_clerk.write_event.assert_called_once()
        kw = self.mock_clerk.write_event.call_args.kwargs
        self.assertEqual(kw["what"], "checklist:answer")
        self.assertEqual(kw["app"], "controls")
        self.assertEqual(kw["consequence"], {"submission": 45, "q": "Q3"})
        self.assertEqual(kw["status"], "ok")

    def test_who_is_not_taken_from_body(self):
        self.client.post("", json={"what": "x:y", "app": "controls", "who": "hacker"}, headers=AUTH)
        kw = self.mock_clerk.write_event.call_args.kwargs
        self.assertNotEqual(kw["who"], "hacker")   # never trusts the body's "who"

    def test_schema_has_no_who_field(self):
        self.assertNotIn("who", AuditEventIn.model_fields)   # input contract excludes "who" by design

    def test_consequence_and_status_default(self):
        resp = self.client.post("", json={"what": "auth:login", "app": "qualification"}, headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        kw = self.mock_clerk.write_event.call_args.kwargs
        self.assertEqual(kw["consequence"], {})
        self.assertEqual(kw["status"], "ok")

    def test_missing_token_is_rejected(self):
        # No Authorization header at all -> ninja HttpBearer 401s (the door still needs a token presented).
        resp = self.client.post("", json={"what": "x:y", "app": "controls"})
        self.assertEqual(resp.status_code, 401)

    def test_get_audit_returns_verify_and_events(self):
        # GET /audit is admin-gated; with AUTH_ENABLED pinned False, require_role bypasses -> allowed.
        self.mock_clerk.verify.return_value = True
        self.mock_clerk.list_events.return_value = [{"id": 1, "summary": "x did y"}]
        resp = self.client.get("?limit=5", headers=AUTH)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["verified"], True)
        self.assertEqual(body["events"], [{"id": 1, "summary": "x did y"}])
        self.mock_clerk.list_events.assert_called_once_with(limit=5)
        self.mock_clerk.verify.assert_called_once()
