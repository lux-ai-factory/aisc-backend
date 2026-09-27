"""Installing a plugin is an administrative act.

An install fetches a distribution from an index and the engine then runs its
code. An account that can do that can run code on the server, so it takes the
admin role, the same as the audit log and for a stronger reason.

The suite runs with AUTH_ENABLED False, which is the switch that lets local work
happen without Keycloak, so these tests turn it on themselves. That is also why
they are worth having: with it off, nothing about roles is exercised anywhere.
"""
import datetime
import unittest.mock as mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.plugin import router as plugin_router

ISSUER = "http://keycloak:8080/realms/aisc"

client = TestAsyncClient(plugin_router)

def writes(project_uuid):
    """The three routes that install, reinstall or remove code. Every other
    route on this router reads."""
    return (
        ("post", "", {"package_name": "x", "version": "1.0", "project_uuid": str(project_uuid)}),
        ("post", "/refresh", {"package_name": "x", "version": "1.0", "project_uuid": str(project_uuid)}),
        ("delete", "", {"package_name": "x", "project_uuid": str(project_uuid)}),
    )


class _FakeKey:
    def __init__(self, key):
        self.key = key


class PluginWritesNeedAdminTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name="guard", status=ProjectStatus.Created)
        cls.writes = writes(cls.project.pid)

    def setUp(self):
        fake_client = mock.Mock(
            get_signing_key_from_jwt=mock.Mock(return_value=_FakeKey(self.private_key.public_key()))
        )
        for patcher in (
            mock.patch.object(keycloak, "AUTH_ENABLED", True),
            mock.patch.object(keycloak, "KEYCLOAK_ISSUER", ISSUER),
            mock.patch.object(keycloak, "_get_jwks_client", return_value=fake_client),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def token(self, roles):
        now = datetime.datetime.now(datetime.timezone.utc)
        return jwt.encode(
            {
                "sub": "00000000-0000-0000-0000-000000000009",
                "preferred_username": "someone",
                "iss": ISSUER,
                "iat": now,
                "exp": now + datetime.timedelta(minutes=5),
                "realm_access": {"roles": list(roles)},
            },
            self.private_key,
            algorithm="RS256",
        )

    async def call(self, method, path, body, headers=None):
        return await getattr(client, method)(path, json=body, headers=headers or {})

    async def test_no_token_is_refused(self):
        for method, path, body in self.writes:
            with self.subTest(route=f"{method} {path}"):
                self.assertEqual((await self.call(method, path, body)).status_code, 401)

    async def test_a_signed_in_non_admin_is_refused(self):
        headers = {"Authorization": f"Bearer {self.token(['primary-user'])}"}
        for method, path, body in self.writes:
            with self.subTest(route=f"{method} {path}"):
                response = await self.call(method, path, body, headers)
                self.assertEqual(
                    response.status_code,
                    403,
                    "a signed-in account that may not install code gets 403, not 401: "
                    "it has already said who it is",
                )

    async def test_an_admin_is_not_stopped_by_the_guard(self):
        """Past the guard the call runs and answers on its own merits, which is
        the point: the refusal is no longer about the role. The index is stubbed
        out because what is under test is the guard, not the installer."""
        headers = {"Authorization": f"Bearer {self.token(['admin'])}"}
        with mock.patch(
            "aisc_backend.routers.plugin.plugin_loader.load_package", return_value={}
        ), mock.patch(
            "aisc_backend.routers.plugin.plugin_loader.refresh_package", return_value={}
        ):
            for method, path, body in self.writes:
                with self.subTest(route=f"{method} {path}"):
                    response = await self.call(method, path, body, headers)
                    self.assertNotIn(response.status_code, (401, 403))

    async def test_the_gateway_header_carries_the_role_too(self):
        headers = {keycloak.GATEWAY_TOKEN_HEADER: self.token(["primary-user"])}
        response = await self.call("post", "", self.writes[0][2], headers)
        self.assertEqual(response.status_code, 403)

    async def test_reading_the_installed_plugins_takes_no_admin(self):
        headers = {"Authorization": f"Bearer {self.token(['primary-user'])}"}
        response = await client.get("", headers=headers)
        self.assertNotIn(response.status_code, (401, 403))


class RoleRefusalIsForbiddenNotUnauthorisedTestCase(TestCase):
    """A verified token that lacks the role is 403 everywhere on the platform.

    401 invites a client to sign in again, which cannot help and makes a
    permissions problem look like a session problem in the logs.
    """

    def test_require_role_refuses_with_403(self):
        from ninja.errors import HttpError

        auth = keycloak.require_role("admin")
        with mock.patch.object(keycloak, "AUTH_ENABLED", True), mock.patch.object(
            keycloak, "verify_token", return_value={"sub": "x", "realm_access": {"roles": ["primary-user"]}}
        ):
            with self.assertRaises(HttpError) as raised:
                auth.authenticate(mock.Mock(headers={}), "a-token")
        self.assertEqual(raised.exception.status_code, 403)


class AuthDisabledIsStillADevelopmentBypassTestCase(TestCase):
    """AUTH_ENABLED off means local work needs no Keycloak: any token is taken
    at face value and no role is checked. It does not mean no token at all, and
    three other tests hold that line, because the API is deny-by-default even in
    development."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name="bypass", status=ProjectStatus.Created)

    async def test_any_token_is_enough_when_auth_is_off(self):
        with mock.patch.object(keycloak, "AUTH_ENABLED", False), mock.patch(
            "aisc_backend.routers.plugin.plugin_loader.load_package", return_value={}
        ):
            response = await client.post(
                "",
                json={"package_name": "x", "version": "1", "project_uuid": str(self.project.pid)},
                headers={"Authorization": "Bearer development"},
            )
        self.assertNotIn(response.status_code, (401, 403))

    async def test_no_token_is_still_refused_when_auth_is_off(self):
        with mock.patch.object(keycloak, "AUTH_ENABLED", False):
            response = await client.post("", json={})
        self.assertEqual(response.status_code, 401)
