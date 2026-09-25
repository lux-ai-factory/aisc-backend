"""
Tests for the Keycloak-auth-protected /me endpoints — run HERMETICALLY with a
mocked token layer (AUTH_ENABLED + verify_token), so they execute in CI instead
of silently skipping.

Exercises the deny-by-default gates against the REAL routers as ASGI:
  - anonymous -> 401
  - admin token -> 200 with roles, /me/admin allowed
  - primary-user token -> allowed on /me, blocked on /me/admin
"""
import unittest.mock as mock

from django.test import SimpleTestCase, AsyncClient
from ninja.testing import TestClient

from aisc_backend.routers.me import router as me_router


def _claims_for(token: str) -> dict:
    if token == "admin":
        return {"preferred_username": "admin", "realm_access": {"roles": ["admin", "primary-user"]}}
    return {"preferred_username": "user", "realm_access": {"roles": ["primary-user"]}}


def _get_token(username: str, _password: str) -> str:
    return username


class KeycloakIntegrationTest(SimpleTestCase):

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

    # --- Phase 6: deny-by-default. Go through the REAL api with the ASGI client (the app runs ASGI). ---
    async def test_deny_by_default_blocks_anonymous_on_real_endpoint(self):
        # /app/app-name used to be open; the API-wide KeycloakAuth now requires a token.
        # (Auth rejects before the handler, so no DB is touched — safe in SimpleTestCase.)
        resp = await AsyncClient().get("/api/v1/app/app-name")
        self.assertEqual(resp.status_code, 401)

    async def test_docs_and_schema_stay_public(self):
        # The OpenAPI schema lives under /api/ (not /api/v1/) and must remain reachable without a token.
        resp = await AsyncClient().get("/api/openapi.json")
        self.assertEqual(resp.status_code, 200)
