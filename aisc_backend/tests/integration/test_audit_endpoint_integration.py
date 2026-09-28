"""
Tests for the /audit endpoint — run HERMETICALLY with a mocked Keycloak token
layer and an in-memory fake immudb client, so they execute in CI instead of
silently skipping.

Proves the door->clerk->ledger chain: POST /audit writes an event with the
identity read FROM the verified token (never the body), and the admin-gated GET
reads + verifies the ledger.
"""
import unittest.mock as mock

from django.test import SimpleTestCase
from ninja.testing import TestClient

from aisc_backend.routers.audit import router as audit_router
from aisc_backend.audit.clerk import clerk as audit_clerk
from aisc_backend.tests.immudb.fake_immudb import FakeImmudbClient


def _claims_for(token: str) -> dict:
    # admin token -> admin realm role; anything else -> primary-user only
    if token == "admin":
        return {"preferred_username": "admin", "realm_access": {"roles": ["admin", "primary-user"]}}
    return {"preferred_username": "user", "realm_access": {"roles": ["primary-user"]}}


def _get_token(username: str, _password: str) -> str:
    return username


class AuditEndpointIntegrationTest(SimpleTestCase):

    def setUp(self):
        self.auth_enabled = mock.patch("aisc_backend.auth.keycloak.AUTH_ENABLED", True)
        self.auth_enabled.start()
        self.addCleanup(self.auth_enabled.stop)
        self.verify_token = mock.patch(
            "aisc_backend.auth.keycloak.verify_token",
            side_effect=lambda token: _claims_for(token),
        )
        self.verify_token.start()
        self.addCleanup(self.verify_token.stop)
        self.immudb_patch = mock.patch("aisc_backend.audit.clerk.ImmudbClient", FakeImmudbClient)
        self.immudb_patch.start()
        self.addCleanup(self.immudb_patch.stop)
        self.client = TestClient(audit_router)

    def test_post_with_real_token_writes_event_with_token_identity(self):
        token = _get_token("admin", "admin")
        marker = "endpoint_itest_probe"   # unique resource_type
        resp = self.client.post(
            "", json={"action": "probe", "resource_type": marker, "resource_id": "e-1",
                      "source_app": "controls", "metadata": {"x": 1}},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["actor"], "admin")   # identity came FROM the verified token

        # confirm it actually landed in immudb with actor=admin
        audit_clerk.connect()
        rows = audit_clerk._client.sqlQuery(
            "SELECT actor, action, source_app FROM audit_log WHERE resource_type = @rt;",
            params={"rt": marker})
        self.assertTrue(rows)
        actor, action, app = rows[-1]
        self.assertEqual(actor, "admin")
        self.assertEqual(app, "controls")

    def test_get_audit_admin_only(self):
        # ADMIN: can read + verify the ledger
        admin = _get_token("admin", "admin")
        resp = self.client.get("", headers={"Authorization": f"Bearer {admin}"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("events", body)
        self.assertTrue(body["verified"])
        self.assertIsInstance(body["events"], list)

        # NON-admin (primary-user): rejected by require_role("admin")
        user = _get_token("user", "user")
        resp2 = self.client.get("", headers={"Authorization": f"Bearer {user}"})
        self.assertIn(resp2.status_code, (401, 403))
