"""Installing a plugin is an administrative act.

An install fetches a distribution from an index and the engine then runs its
code. An account that can do that can run code on the server, so in the
Configurator it takes the admin role, the same as the audit log and for a
stronger reason.

The suite runs with AUTH_ENABLED False, which is the switch that lets local work
happen without Keycloak, so these tests turn it on themselves. That is also why
they are worth having: with it off, nothing about roles is exercised anywhere.

The admin rule is the Configurator's and, since the adapt plan (2026-09-28, item 2), the
door's: Sean's plugin router is his again, and standalone keeps his rule (any signed-in user
installs). A verified token that lacks a role on a `require_role` route is Sean's 401 (item 3).
"""
import datetime
import unittest.mock as mock
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.plugin import router as plugin_router
from aisc_backend.tests.isolation_support import configurator_only
from aisc_backend.tests.test_isolation_engine import A_PID, DoorCase

ISSUER = "http://keycloak:8080/realms/aisc"

client = TestAsyncClient(plugin_router)


def writes(project_uuid):
    """The three routes that install, reinstall or remove code, as the door sees them.
    Every other route on this router reads."""
    body = {"package_name": "x", "version": "1.0", "project_uuid": str(project_uuid)}
    return (
        ("POST", "/api/v1/plugins", body),
        ("POST", "/api/v1/plugins/refresh", body),
        ("DELETE", "/api/v1/plugins", body),
    )


class _FakeKey:
    def __init__(self, key):
        self.key = key


@configurator_only
class PluginWritesNeedAdminAtTheDoor(DoorCase):
    """Through the middleware, membership stubbed: an editor of the project is not enough."""

    WRITES = writes(uuid.uuid4())

    def test_no_token_is_refused(self):
        for method, path, body in self.WRITES:
            with self.subTest(route=f"{method} {path}"):
                response = self.call(method, path, {"X-AISC-Project": A_PID}, body)
                self.assertEqual(response.status_code, 401)

    def test_a_signed_in_non_admin_is_refused(self):
        for method, path, body in self.WRITES:
            with self.subTest(route=f"{method} {path}"), self.as_role("editor"):
                response = self.call(method, path, {"X-AISC-Project": A_PID, "Authorization": self.bearer()}, body)
                self.assertEqual(
                    response.status_code,
                    403,
                    "a signed-in account that may not install code gets 403, not 401: "
                    "it has already said who it is",
                )

    def test_the_gateway_header_carries_the_role_too(self):
        method, path, body = self.WRITES[0]
        token = self.bearer().removeprefix("Bearer ")
        with self.as_role("editor"):
            response = self.call(method, path, {"X-AISC-Project": A_PID, "X-Auth-Request-Access-Token": token},
                                 body)
        self.assertEqual(response.status_code, 403)

    def test_an_admin_is_not_stopped_by_the_rule(self):
        """Past the rule the door goes on to open the project's database, which is
        the point: the refusal is no longer about the role."""
        from django.db import DatabaseError

        from aisc_backend import projectdb

        for method, path, body in self.WRITES:
            with self.subTest(route=f"{method} {path}"), mock.patch.object(
                    projectdb, "alias_for", side_effect=DatabaseError("stop: the door opened it")) as opened:
                response = self.call(method, path, {"X-AISC-Project": A_PID,
                                                    "Authorization": self.bearer(("admin",))}, body)
                self.assertEqual(response.status_code, 503)
                opened.assert_called_once()


@configurator_only
class ReadingTheInstalledPluginsTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

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

    async def test_reading_the_installed_plugins_takes_no_admin(self):
        headers = {"Authorization": f"Bearer {self.token(['primary-user'])}"}
        response = await client.get("", headers=headers)
        self.assertNotIn(response.status_code, (401, 403))


class RoleRefusalIsSeans401TestCase(TestCase):
    """A verified token that lacks the role: `require_role` answers None, which is 401 (Sean's)."""

    def test_require_role_refuses_with_none(self):
        auth = keycloak.require_role("admin")
        with mock.patch.object(keycloak, "AUTH_ENABLED", True), mock.patch.object(
            keycloak, "verify_token", return_value={"sub": "x", "realm_access": {"roles": ["primary-user"]}}
        ):
            self.assertIsNone(auth.authenticate(mock.Mock(headers={}), "a-token"))


@configurator_only
class AuthDisabledIsStillADevelopmentBypassTestCase(TestCase):
    """AUTH_ENABLED off means local work needs no Keycloak: any token is taken
    at face value and no role is checked. It does not mean no token at all: the
    API is deny-by-default even in development (config.urls, KeycloakAuth as its default)."""

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
        """Through the whole stack (the door, then the API's KeycloakAuth default): no token is
        401 even with AUTH_ENABLED off. On sqlite the door admits the one test database."""
        from django.test import AsyncClient

        with mock.patch.object(keycloak, "AUTH_ENABLED", False):
            response = await AsyncClient(raise_request_exception=False).post(
                "/api/v1/plugins", data={}, content_type="application/json",
                headers={"X-AISC-Project": A_PID})
        self.assertEqual(response.status_code, 401)
