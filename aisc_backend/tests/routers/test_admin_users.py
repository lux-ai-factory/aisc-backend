"""
Tests for the admin-user surface (backend -> Keycloak Admin REST).

The Keycloak admin client is stubbed (`services.keycloak_admin` methods are
patched) so these run hermetically (no realm, no network). Auth is the
dev-bypass mode (`require_role("admin")` passes everything through), the same
posture as the admin-only /audit door's tests. Also asserts the service fails
closed when the admin client is unconfigured.
"""
import asyncio
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.routers.admin_users import router as admin_router
from aisc_backend.services import keycloak_admin

client = TestAsyncClient(admin_router)

USER = {"id": "kc-1", "username": "jdoe", "email": "j@x.io", "enabled": True}


class AdminUsersRouterTest(TestCase):

    async def test_list_users_search(self):
        with mock.patch(
            "aisc_backend.services.keycloak_admin.list_users",
            new=mock.AsyncMock(return_value=[USER]),
        ) as lu:
            resp = await client.get("?search=jdoe")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), [USER])
        lu.assert_awaited_once_with(search="jdoe")

    async def test_get_user(self):
        with mock.patch(
            "aisc_backend.services.keycloak_admin.get_user",
            new=mock.AsyncMock(return_value=USER),
        ):
            resp = await client.get("/kc-1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "jdoe")

    async def test_get_missing_user_404(self):
        with mock.patch(
            "aisc_backend.services.keycloak_admin.get_user",
            new=mock.AsyncMock(return_value=None),
        ):
            resp = await client.get("/nope")
        self.assertEqual(resp.status_code, 404)

    async def test_create_user(self):
        payload = {"username": "JDOE", "email": "j@x.io", "enabled": True}
        with mock.patch(
            "aisc_backend.services.keycloak_admin.create_user",
            new=mock.AsyncMock(return_value={"id": "kc-2"}),
        ) as cu:
            resp = await client.post("", json=payload)
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["id"], "kc-2")
        # username is lowercased before it reaches Keycloak
        sent = cu.await_args.args[0]
        self.assertEqual(sent["username"], "jdoe")

    async def test_update_user(self):
        with mock.patch(
            "aisc_backend.services.keycloak_admin.update_user",
            new=mock.AsyncMock(),
        ) as uu, mock.patch(
            "aisc_backend.services.keycloak_admin.get_user",
            new=mock.AsyncMock(return_value={**USER, "enabled": False}),
        ):
            resp = await client.patch("/kc-1", json={"enabled": False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["enabled"], False)
        sent = uu.await_args
        self.assertEqual(sent.args[0], "kc-1")
        self.assertEqual(sent.args[1], {"enabled": False})

    async def test_reset_password(self):
        with mock.patch(
            "aisc_backend.services.keycloak_admin.reset_password",
            new=mock.AsyncMock(),
        ) as rp:
            resp = await client.post("/kc-1/reset-password", json={"password": "s3cret"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(rp.await_args.args[2])   # temporary defaults True

    async def test_assign_and_revoke_realm_role(self):
        with mock.patch(
            "aisc_backend.services.keycloak_admin.assign_realm_role",
            new=mock.AsyncMock(),
        ) as ar:
            resp = await client.post("/kc-1/roles/admin")
        self.assertEqual(resp.status_code, 200)
        ar.assert_awaited_once_with("kc-1", "admin")

        with mock.patch(
            "aisc_backend.services.keycloak_admin.revoke_realm_role",
            new=mock.AsyncMock(),
        ):
            resp = await client.delete("/kc-1/roles/admin")
        self.assertEqual(resp.status_code, 204)


class KeycloakAdminClientFailClosedTest(TestCase):
    """With the admin client unconfigured the service refuses, never guesses."""

    def test_configured_is_false_when_url_or_secret_missing(self):
        with mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_URL", ""), \
             mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_CLIENT_SECRET", ""):
            self.assertFalse(keycloak_admin._configured())

    def test_grant_token_raises_when_unconfigured(self):
        with mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_URL", ""), \
             mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_CLIENT_SECRET", ""):
            async def _run():
                await keycloak_admin._grant_token()
            with self.assertRaises(keycloak_admin.KeycloakAdminUnavailable):
                asyncio.run(_run())

    def test_create_user_raises_when_unconfigured(self):
        with mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_URL", ""), \
             mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_CLIENT_SECRET", ""):
            async def _run():
                await keycloak_admin.create_user({"username": "x"})
            with self.assertRaises(keycloak_admin.KeycloakAdminUnavailable):
                asyncio.run(_run())

    def test_list_users_is_empty_when_unconfigured(self):
        with mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_URL", ""), \
             mock.patch("aisc_backend.services.keycloak_admin.KEYCLOAK_ADMIN_CLIENT_SECRET", ""):
            self.assertEqual(asyncio.run(keycloak_admin.list_users()), [])
