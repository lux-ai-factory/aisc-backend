"""The routes, one behaviour per mode where the spec says so.

Standalone is Sean's engine: it makes and lists its own projects, needs no
project header, and the worker gets the evaluation pid alone. The configurator
never makes a project (the launcher does) and follows the platform's memberships.
What the worker is sent, in both modes, is in test_celery_dispatch.py.

The mode is read from settings, like everything else that asks it.
"""
import datetime
import unittest
import unittest.mock as mock
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.test import TestCase
from ninja.testing import TestAsyncClient, TestClient

from aisc_backend.auth import keycloak
from aisc_backend.routers.project import router as projects_router
from aisc_backend.tests.isolation_support import configurator_only
from aisc_backend.tests.test_isolation_engine import A_PID as DOOR_PID, DoorCase

CONFIGURATOR = settings.AISC_DEPLOYMENT == "configurator"
standalone_run = unittest.skipIf(CONFIGURATOR, "standalone run")
configurator_run = unittest.skipUnless(CONFIGURATOR, "configurator run")

ISSUER = "http://keycloak:8080/realms/aisc"
A_PID = "00000000-0000-0000-0000-000000000001"


class _FakeKey:
    def __init__(self, key):
        self.key = key


class SignedIn(TestCase):
    """Authentication on, with a key of our own standing in for the realm's."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        fake_client = mock.Mock(
            get_signing_key_from_jwt=mock.Mock(return_value=_FakeKey(self.private_key.public_key())))
        for patcher in (
            mock.patch.object(keycloak, "AUTH_ENABLED", True),
            mock.patch.object(keycloak, "KEYCLOAK_ISSUER", ISSUER),
            mock.patch.object(keycloak, "_get_jwks_client", return_value=fake_client),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def token(self, roles=("primary-user",)):
        now = datetime.datetime.now(datetime.timezone.utc)
        return jwt.encode(
            {"sub": "someone", "iss": ISSUER, "iat": now,
             "exp": now + datetime.timedelta(minutes=5),
             "realm_access": {"roles": list(roles)}},
            self.private_key, algorithm="RS256")


class StandaloneProjects(TestCase):
    @standalone_run
    async def test_a_project_is_made_in_the_engine_and_listed(self):
        client = TestAsyncClient(projects_router)
        made = await client.post("", json={"name": "Loans"})
        self.assertEqual(made.status_code, 200, made.content)
        listed = await client.get("")
        self.assertIn("Loans", [p["name"] for p in listed.json()])

    @standalone_run
    async def test_no_project_header_is_needed(self):
        response = await TestAsyncClient(projects_router).get("")
        self.assertEqual(response.status_code, 200)

    @standalone_run
    async def test_there_is_no_for_platform_route(self):
        # Through the whole API: ninja's test clients cannot answer an unknown path.
        response = await self.async_client.post(
            "/api/v1/projects/for-platform/00000000-0000-0000-0000-000000000001")
        self.assertEqual(response.status_code, 404)


class ConfiguratorProjects(TestCase):
    @configurator_run
    async def test_the_first_visit_makes_the_engine_s_row_and_the_next_finds_it(self):
        from aisc_backend.models import Project
        from aisc_backend.routers.platform_project import router as platform_project_router

        client = TestAsyncClient(platform_project_router)
        with mock.patch.object(keycloak, "AUTH_ENABLED", False):
            first = await client.post(f"/{A_PID}")
            again = await client.post(f"/{A_PID}")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["pid"], again.json()["pid"])
        self.assertEqual(await Project.objects.filter(platform_project_id=A_PID).acount(), 1)


@configurator_only
class ConfiguratorProjectsAtTheDoor(DoorCase):
    """The engine does not make projects: the door's 403 (adapt plan 2026-09-28, item 2)."""

    def test_the_engine_does_not_make_projects(self):
        with self.as_role("owner"):
            response = self.call("POST", "/api/v1/projects",
                                 {"X-AISC-Project": DOOR_PID, "Authorization": self.bearer()}, {"name": "Loans"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("launcher", response.json()["detail"])


class TheGatewayHeader(SignedIn):
    """oauth2-proxy's header is the Configurator's session; standalone has its own login.
    The constant is the door's (adapt plan 2026-09-28, item 2); Sean's `keycloak.py` never
    reads it, and the door copies it into `Authorization` (test_gateway_session)."""

    def router(self):
        from ninja import Router

        router = Router(auth=keycloak.KeycloakAuth())

        @router.get("/who")
        def who(request):
            return {"sub": request.auth.get("sub") if isinstance(request.auth, dict) else None}

        return TestClient(router)

    def test_the_constant_is_the_door_s(self):
        from aisc_backend import project_door

        self.assertEqual(project_door.GATEWAY_TOKEN_HEADER, "X-Auth-Request-Access-Token")
        self.assertFalse(hasattr(keycloak, "GATEWAY_TOKEN_HEADER"))

    def test_sean_s_bearer_check_does_not_take_it(self):
        response = self.router().get(
            "/who", headers={"X-Auth-Request-Access-Token": self.token()})
        self.assertEqual(response.status_code, 401)


class AMissingRoleIsSeans401(SignedIn):
    """A verified token without the role is Sean's 401 in both modes (adapt plan item 3)."""

    def test_a_signed_in_non_admin_is_401_on_an_admin_route(self):
        from aisc_backend.routers.audit import router as audit_router

        response = TestClient(audit_router).get(
            "", headers={"Authorization": f"Bearer {self.token()}"})
        self.assertEqual(response.status_code, 401)


class InstallingAPlugin(SignedIn):
    """Standalone keeps Sean's rule: any signed-in user installs. In the Configurator an
    install runs code on the shared server, so it takes admin: the door's rule
    (InstallingAPluginAtTheDoor)."""

    async def _install(self):
        from aisc_backend.models import Project, ProjectStatus
        from aisc_backend.routers.plugin import router as plugin_router

        project = await Project.objects.acreate(name="p", status=ProjectStatus.Created)
        with mock.patch("aisc_backend.routers.plugin.plugin_loader.load_package", return_value={}):
            return await TestAsyncClient(plugin_router).post(
                "", json={"package_name": "x", "version": "1", "project_uuid": str(project.pid)},
                headers={"Authorization": f"Bearer {self.token()}"})

    @standalone_run
    async def test_standalone_lets_a_signed_in_user_install(self):
        self.assertNotIn((await self._install()).status_code, (401, 403))


@configurator_only
class InstallingAPluginAtTheDoor(DoorCase):
    def test_the_configurator_takes_admin(self):
        with self.as_role("editor"):
            response = self.call("POST", "/api/v1/plugins", {"X-AISC-Project": DOOR_PID,
                                                             "Authorization": self.bearer()},
                                 {"package_name": "x", "version": "1", "project_uuid": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)


@configurator_only
class TheRoutesTheEnumerationMissed(DoorCase):
    """A project by its name, and a plugin's result in one evaluation: in the Configurator a
    stranger is told there is no such thing (the door's 404). That a member reads them is on
    Postgres (test_isolation_engine_db CONTROLS, test_isolation_result_route)."""

    def headers(self):
        return {"X-AISC-Project": DOOR_PID, "Authorization": self.bearer()}

    def test_a_stranger_does_not_find_a_project_by_its_name(self):
        with self.as_role(None):
            refused = self.call("GET", "/api/v1/projects/by-name/theirs", self.headers())
        self.assertEqual(refused.status_code, 404)

    def test_a_stranger_does_not_read_a_result_by_its_ids(self):
        with self.as_role(None):
            refused = self.call("GET", f"/api/v1/plugins/{uuid.uuid4()}/evaluations/{uuid.uuid4()}/result",
                                self.headers())
        self.assertEqual(refused.status_code, 404)
