"""
INTEGRATION test for the /audit endpoint — requires RUNNING keycloak (for a real token) AND immudb.

Proves the full door→clerk→ledger chain with a REAL token: POST /audit with an admin Bearer token,
and the event lands in immudb with who="admin" (read FROM the verified token, not the body).

Run (services up):
  AUTH_ENABLED=true KEYCLOAK_ISSUER=http://localhost:8081/realms/aisc \
  KEYCLOAK_JWKS_URL=http://localhost:8081/realms/aisc/protocol/openid-connect/certs \
  KC_REALM=http://localhost:8081/realms/aisc IMMUDB_URL=localhost:3322 IMMUDB_ADMIN_PASSWORD=immudbDev1! \
  uv run manage.py test aisc_backend.tests.integration.test_audit_endpoint_integration
"""
import json
import os
import unittest
import urllib.parse
import urllib.request

from django.conf import settings
from django.test import SimpleTestCase
from ninja.testing import TestClient

from immudb import ImmudbClient
from aisc_backend.routers.audit import router as audit_router
from aisc_backend.audit.clerk import AuditClerk

KC_REALM = os.environ.get("KC_REALM", "http://localhost:8081/realms/aisc")


def _get_token(username, password):
    body = urllib.parse.urlencode({
        "client_id": "aisc-webapp", "grant_type": "password",
        "username": username, "password": password,
    }).encode()
    url = f"{KC_REALM}/protocol/openid-connect/token"
    with urllib.request.urlopen(urllib.request.Request(url, data=body)) as r:
        return json.load(r)["access_token"]


def _services_up():
    try:
        urllib.request.urlopen(KC_REALM + "/.well-known/openid-configuration", timeout=2)
        c = ImmudbClient(settings.IMMUDB_URL); c.login(settings.IMMUDB_USER, settings.IMMUDB_PASSWORD); c.logout()
        return True
    except Exception:
        return False


@unittest.skipUnless(_services_up(), "keycloak and/or immudb not reachable")
class AuditEndpointIntegrationTest(SimpleTestCase):
    def setUp(self):
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
        clerk = AuditClerk(); clerk.connect()
        rows = clerk._client.sqlQuery(
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
        self.assertTrue(body["verified"])              # cryptographic VerifiedGet proof passes
        self.assertIsInstance(body["events"], list)

        # NON-admin (primary-user): rejected by require_role("admin")
        user = _get_token("user", "user")
        resp2 = self.client.get("", headers={"Authorization": f"Bearer {user}"})
        self.assertIn(resp2.status_code, (401, 403))
