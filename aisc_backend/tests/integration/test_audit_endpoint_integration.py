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
        marker = "endpoint-itest:probe"
        resp = self.client.post(
            "", json={"what": marker, "app": "controls", "consequence": {"x": 1}},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["who"], "admin")   # identity came FROM the verified token

        # confirm it actually landed in immudb with who=admin
        clerk = AuditClerk(); clerk.connect()
        rows = clerk._client.sqlQuery(
            "SELECT who, what, app FROM audit_log WHERE what = @w;", params={"w": marker})
        self.assertTrue(rows)
        who, what, app = rows[-1]
        self.assertEqual(who, "admin")
        self.assertEqual(app, "controls")
