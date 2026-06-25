"""
INTEGRATION tests — require a RUNNING Keycloak with the `aisc` realm.
Auto-skips if Keycloak isn't reachable. Uses REAL password-grant tokens against the /me router.

Run (Keycloak up), with settings pointing at the SAME host the tokens come from:
    AUTH_ENABLED=true \
    KEYCLOAK_ISSUER=http://localhost:8081/realms/aisc \
    KEYCLOAK_JWKS_URL=http://localhost:8081/realms/aisc/protocol/openid-connect/certs \
    uv run manage.py test aisc_backend.tests.integration.test_keycloak_integration
"""
import json
import os
import unittest
import urllib.parse
import urllib.request

from django.test import SimpleTestCase
from ninja.testing import TestClient

from aisc_backend.routers.me import router as me_router

KC_REALM = os.environ.get("KC_REALM", "http://localhost:8081/realms/aisc")
TOKEN_URL = f"{KC_REALM}/protocol/openid-connect/token"


def _get_token(username: str, password: str) -> str:
    body = urllib.parse.urlencode({
        "client_id": "aisc-webapp",
        "grant_type": "password",
        "username": username,
        "password": password,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as r:
        return json.load(r)["access_token"]


def _keycloak_up() -> bool:
    try:
        urllib.request.urlopen(KC_REALM + "/.well-known/openid-configuration", timeout=2)
        return True
    except Exception:
        return False


@unittest.skipUnless(_keycloak_up(), "Keycloak not reachable on KC_REALM")
class KeycloakIntegrationTest(SimpleTestCase):
    def setUp(self):
        self.client = TestClient(me_router)

    def test_no_token_returns_401(self):
        resp = self.client.get("")  # no Authorization header
        self.assertEqual(resp.status_code, 401)

    def test_valid_admin_token_returns_200_with_roles(self):
        token = _get_token("admin", "admin")
        resp = self.client.get("", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "admin")
        self.assertIn("admin", resp.json()["roles"])

    def test_primary_user_blocked_on_admin_endpoint(self):
        token = _get_token("user", "user")
        resp = self.client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        self.assertIn(resp.status_code, (401, 403))  # lacks the admin role

    def test_primary_user_allowed_on_normal_endpoint(self):
        token = _get_token("user", "user")
        resp = self.client.get("", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "user")
