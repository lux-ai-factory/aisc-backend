"""The routes, one behaviour per mode where the spec says so.

Standalone is Sean's engine: it makes and lists its own projects, needs no
project header, and the worker gets the evaluation pid alone. The configurator
never makes a project (the launcher does), follows the platform's memberships,
and hands the worker the platform pid and a run ticket.

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
    async def test_the_engine_does_not_make_projects(self):
        # The router names the bearer check in configurator, so a call carries a
        # token; AUTH_ENABLED is off here, so it is not verified.
        response = await TestAsyncClient(projects_router).post(
            "", json={"name": "Loans"},
            headers={"X-AISC-Project": A_PID, "Authorization": "Bearer development"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("launcher", response.json()["detail"])

    @configurator_run
    async def test_the_first_visit_makes_the_engine_s_row_and_the_next_finds_it(self):
        from aisc_backend.models import Project

        client = TestAsyncClient(projects_router)
        headers = {"Authorization": "Bearer development"}
        with mock.patch.object(keycloak, "AUTH_ENABLED", False):
            first = await client.post(f"/for-platform/{A_PID}", headers=headers)
            again = await client.post(f"/for-platform/{A_PID}", headers=headers)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["pid"], again.json()["pid"])
        self.assertEqual(await Project.objects.filter(platform_project_id=A_PID).acount(), 1)


class TheGatewayHeader(SignedIn):
    """oauth2-proxy's header is the Configurator's session; standalone has its own login."""

    def router(self):
        from ninja import Router

        router = Router(auth=keycloak.KeycloakAuth())

        @router.get("/who")
        def who(request):
            return {"sub": request.auth.get("sub") if isinstance(request.auth, dict) else None}

        return TestClient(router)

    def test_the_constant_exists_in_both_modes(self):
        self.assertEqual(keycloak.GATEWAY_TOKEN_HEADER, "X-Auth-Request-Access-Token")

    @standalone_run
    def test_standalone_does_not_take_it(self):
        response = self.router().get(
            "/who", headers={keycloak.GATEWAY_TOKEN_HEADER: self.token()})
        self.assertEqual(response.status_code, 401)

    @configurator_run
    def test_the_configurator_takes_it(self):
        response = self.router().get(
            "/who", headers={keycloak.GATEWAY_TOKEN_HEADER: self.token()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["sub"], "someone")


class AMissingRoleIsForbidden(SignedIn):
    """A verified token without the role is 403 in both modes, not 401."""

    def test_a_signed_in_non_admin_is_403_on_an_admin_route(self):
        from aisc_backend.routers.audit import router as audit_router

        response = TestClient(audit_router).get(
            "", headers={"Authorization": f"Bearer {self.token()}"})
        self.assertEqual(response.status_code, 403)


class InstallingAPlugin(SignedIn):
    """In the Configurator an install runs code on the shared server, so it takes admin.
    Standalone keeps Sean's rule: any signed-in user."""

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

    @configurator_run
    async def test_the_configurator_takes_admin(self):
        self.assertEqual((await self._install()).status_code, 403)


class StartingARun(TestCase):
    """What the worker is sent."""

    async def _dispatched_args(self):
        from aisc_backend.models import Evaluation, EvaluationStatus, Project, ProjectStatus
        from aisc_backend.services import celery_service

        project = await Project.objects.acreate(name="p", status=ProjectStatus.Created,
                                                platform_project_id=A_PID)
        evaluation = await Evaluation.objects.acreate(project=project, status=EvaluationStatus.Pending)
        send = mock.Mock(return_value=mock.Mock(task_id=str(uuid.uuid4())))
        with mock.patch.object(celery_service.celery, "send_task", new=send):
            await celery_service.run_evaluation(evaluation.pid)
        return evaluation.pid, send.call_args.kwargs.get("args") or send.call_args.args[1]

    @standalone_run
    async def test_standalone_sends_the_evaluation_alone(self):
        evaluation_pid, args = await self._dispatched_args()
        self.assertEqual(args, [evaluation_pid])

    @configurator_run
    async def test_the_configurator_sends_the_project_and_a_ticket(self):
        from aisc_backend import projectdb

        evaluation_pid, args = await self._dispatched_args()
        pid = projectdb.normalise(A_PID)
        self.assertEqual(args, [pid, projectdb.normalise(evaluation_pid),
                                projectdb.run_ticket(pid, projectdb.normalise(evaluation_pid))])


class TheRoutesTheEnumerationMissed(SignedIn):
    """A project by its name, and a plugin's result in one evaluation: in the Configurator a
    stranger is told there is no such thing, a member reads it."""

    @classmethod
    def setUpTestData(cls):
        from aisc_backend.models import Evaluation, EvaluationStatus, Project, ProjectStatus

        cls.project = Project.objects.create(name="theirs", status=ProjectStatus.Created,
                                             platform_project_id=A_PID)
        cls.evaluation = Evaluation.objects.create(project=cls.project, status=EvaluationStatus.Pending)

    def as_role(self, role):
        return mock.patch("aisc_backend.auth.membership.role_in_project", return_value=role)

    def headers(self):
        return {"Authorization": f"Bearer {self.token()}"}

    @configurator_run
    async def test_a_stranger_does_not_find_a_project_by_its_name(self):
        client = TestAsyncClient(projects_router)
        with self.as_role(None):
            refused = await client.get("/by-name/theirs", headers=self.headers())
        with self.as_role("viewer"):
            member = await client.get("/by-name/theirs", headers=self.headers())
            missing = await client.get("/by-name/nobody-has-this", headers=self.headers())
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(member.status_code, 200, member.content)
        self.assertEqual(missing.status_code, 404)

    @configurator_run
    async def test_a_stranger_does_not_read_a_result_by_its_ids(self):
        from aisc_backend.routers.plugin import router as plugin_router

        with self.as_role(None):
            refused = await TestAsyncClient(plugin_router).get(
                f"/{uuid.uuid4()}/evaluations/{self.evaluation.pid}/result", headers=self.headers())
        self.assertEqual(refused.status_code, 404)
