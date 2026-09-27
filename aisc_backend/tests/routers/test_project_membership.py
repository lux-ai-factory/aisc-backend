"""A project belongs to the people in it, here too.

The engine's project row is this service's side of a platform project. Who may
see it, and who may change it, is therefore the platform's answer, read from
`core.project_member` in the one database rather than decided again here.

The suite runs on sqlite, where there is no `core`, so the lookup is stubbed:
what these tests are about is which routes ask and what they do with the
answer. That the query itself works is a Postgres test, below.
"""
import datetime
import unittest.mock as mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connection
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.auth import keycloak
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers.project import router as project_router

ISSUER = "http://keycloak:8080/realms/aisc"
PLATFORM_PROJECT = "11111111-1111-4111-8111-111111111111"

client = TestAsyncClient(project_router)


class _FakeKey:
    def __init__(self, key):
        self.key = key


class ProjectMembershipTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            name="MCAS", status=ProjectStatus.Created, platform_project_id=PLATFORM_PROJECT
        )
        cls.unlinked = Project.objects.create(name="on its own", status=ProjectStatus.Created)

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

    def token(self, roles=("primary-user",), subject="someone"):
        now = datetime.datetime.now(datetime.timezone.utc)
        return jwt.encode(
            {
                "sub": subject,
                "preferred_username": subject,
                "iss": ISSUER,
                "iat": now,
                "exp": now + datetime.timedelta(minutes=5),
                "realm_access": {"roles": list(roles)},
            },
            self.private_key,
            algorithm="RS256",
        )

    def headers(self, roles=("primary-user",), subject="someone"):
        return {"Authorization": f"Bearer {self.token(roles, subject)}"}

    def as_role(self, role):
        """What the shared table says about this caller."""
        return mock.patch("aisc_backend.auth.membership.role_in_project", return_value=role)

    async def test_a_stranger_does_not_see_the_project_in_the_list(self):
        with self.as_role(None):
            response = await client.get(
                f"?platform_project_id={PLATFORM_PROJECT}", headers=self.headers()
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    async def test_a_member_does(self):
        with self.as_role("viewer"):
            response = await client.get(
                f"?platform_project_id={PLATFORM_PROJECT}", headers=self.headers()
            )
        self.assertEqual([p["name"] for p in response.json()], ["MCAS"])

    async def test_listing_without_a_project_shows_only_what_you_are_in(self):
        """The engine used to answer this with every project it had."""
        with self.as_role(None):
            response = await client.get("", headers=self.headers())
        self.assertEqual(response.json(), [])

    async def test_a_stranger_gets_404_on_the_project_itself(self):
        with self.as_role(None):
            response = await client.get(f"/{self.project.pid}", headers=self.headers())
        self.assertEqual(response.status_code, 404)

    async def test_a_member_can_read_it(self):
        with self.as_role("viewer"):
            response = await client.get(f"/{self.project.pid}", headers=self.headers())
        self.assertEqual(response.status_code, 200)

    async def test_a_viewer_cannot_rename_it(self):
        with self.as_role("viewer"):
            response = await client.patch(
                f"/{self.project.pid}", json={"name": "renamed"}, headers=self.headers()
            )
        self.assertEqual(response.status_code, 403)

    async def test_an_editor_can(self):
        with self.as_role("editor"):
            response = await client.patch(
                f"/{self.project.pid}", json={"name": "renamed"}, headers=self.headers()
            )
        self.assertEqual(response.status_code, 200)

    async def test_entering_the_engine_inside_a_project_takes_an_editor(self):
        """The first visit makes the engine's row for the platform project,
        which is work on that project, not a way into it."""
        with self.as_role(None):
            refused = await client.post(f"/for-platform/{PLATFORM_PROJECT}", headers=self.headers())
        self.assertEqual(refused.status_code, 404)

        with self.as_role("viewer"):
            read_only = await client.post(f"/for-platform/{PLATFORM_PROJECT}", headers=self.headers())
        self.assertEqual(read_only.status_code, 403)

        with self.as_role("editor"):
            allowed = await client.post(f"/for-platform/{PLATFORM_PROJECT}", headers=self.headers())
        self.assertEqual(allowed.status_code, 200)

    async def test_an_admin_is_in_every_project(self):
        with self.as_role(None):
            response = await client.get(
                f"/{self.project.pid}", headers=self.headers(roles=("admin",))
            )
        self.assertEqual(response.status_code, 200)

    async def test_a_project_with_no_platform_link_is_for_admins_only(self):
        """Rows made before the platform, or by an engine running on its own.
        Nobody is in them, because there is no project to be in."""
        with self.as_role(None):
            hidden = await client.get(f"/{self.unlinked.pid}", headers=self.headers())
            shown = await client.get(f"/{self.unlinked.pid}", headers=self.headers(roles=("admin",)))
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(shown.status_code, 200)


class MembershipQueryTestCase(TestCase):
    """The query itself, against the database that actually has `core`."""

    def test_reading_a_role_out_of_the_shared_table(self):
        if connection.vendor != "postgresql":
            self.skipTest("core lives in Postgres; the sqlite runner has none")
        from aisc_backend.auth.membership import role_in_project

        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('core.project_member') IS NOT NULL")
            if not cursor.fetchone()[0]:
                self.skipTest("core.project_member is not there yet")
        self.assertIsNone(role_in_project(PLATFORM_PROJECT, "nobody-at-all"))

    def test_no_core_at_all_is_no_role_rather_than_an_error(self):
        """On sqlite there is no core. The guard is switched off with
        AUTH_ENABLED in that case, so this only has to not explode."""
        from aisc_backend.auth.membership import role_in_project

        self.assertIsNone(role_in_project(PLATFORM_PROJECT, "anybody"))


class RunningTestsTakesAnEditorTestCase(TestCase):
    """Running an evaluation is the engine's main act of work on a project."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            name="MCAS", status=ProjectStatus.Created, platform_project_id=PLATFORM_PROJECT
        )

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

    def headers(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode(
            {
                "sub": "someone",
                "iss": ISSUER,
                "iat": now,
                "exp": now + datetime.timedelta(minutes=5),
                "realm_access": {"roles": ["primary-user"]},
            },
            self.private_key,
            algorithm="RS256",
        )
        return {"Authorization": f"Bearer {token}"}

    async def _run(self, role):
        from aisc_backend.routers.evaluation import router as evaluation_router

        evaluations = TestAsyncClient(evaluation_router)
        # The queue is not what is under test. Without this the editor's call
        # goes all the way to RabbitMQ, which is not running in a unit test.
        queued = mock.AsyncMock(return_value=mock.Mock(task_id="22222222-2222-4222-8222-222222222222"))
        with mock.patch("aisc_backend.auth.membership.role_in_project", return_value=role), \
                mock.patch("aisc_backend.routers.evaluation.celery_service.run_evaluation", queued):
            return await evaluations.post(
                "/task",
                json={"project_pid": str(self.project.pid), "plugins_to_run": []},
                headers=self.headers(),
            )

    async def test_a_stranger_is_told_there_is_no_such_project(self):
        self.assertEqual((await self._run(None)).status_code, 404)

    async def test_a_viewer_may_not_run_tests(self):
        self.assertEqual((await self._run("viewer")).status_code, 403)

    async def test_an_editor_may(self):
        """Past the guard it answers on its own merits: an empty plugin list is
        a 400, not a refusal about who is asking."""
        self.assertNotIn((await self._run("editor")).status_code, (401, 403, 404))


class ReachingAProjectByAChildObjectsIdTestCase(TestCase):
    """The enumeration test says every such route asks. This says what the
    answer does: a stranger is refused by an id that is not theirs, and the
    member it belongs to is not."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @classmethod
    def setUpTestData(cls):
        from aisc_backend.models import AIComponent, AIComponentType, AISystem
        from aisc_backend.models.common import StorageContainer
        from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
        from aisc_backend.models.project_config import ProjectConfig, ProjectConfigCategory

        cls.project = Project.objects.create(
            name="theirs", status=ProjectStatus.Created, platform_project_id=PLATFORM_PROJECT
        )
        cls.evaluation = Evaluation.objects.create(
            project=cls.project, status=EvaluationStatus.Pending
        )
        # Since the AISystem merge a dataset is a component of the project's
        # one AI system, not a row of its own.
        system = AISystem.objects.create(project=cls.project, name="their system")
        cls.dataset = AIComponent.objects.create(
            name="a dataset", system=system, component_type=AIComponentType.DATASET,
            data="stored-object-name.csv", storage_container=StorageContainer.Datasets,
        )
        cls.config = ProjectConfig.objects.create(
            project=cls.project, category=ProjectConfigCategory.VARIABLES,
            key="threshold", name="Threshold", json_value={"type": "number", "value": 0.5},
        )

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

    def headers(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode(
            {
                "sub": "someone", "iss": ISSUER, "iat": now,
                "exp": now + datetime.timedelta(minutes=5),
                "realm_access": {"roles": ["primary-user"]},
            },
            self.private_key, algorithm="RS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def as_role(self, role):
        return mock.patch("aisc_backend.auth.membership.role_in_project", return_value=role)

    async def test_an_evaluation_is_not_readable_by_id_alone(self):
        from aisc_backend.routers.evaluation import router as evaluation_router

        evaluations = TestAsyncClient(evaluation_router)
        with self.as_role(None):
            refused = await evaluations.get(f"/{self.evaluation.pid}", headers=self.headers())
        self.assertEqual(refused.status_code, 404)
        # The other half of the pair, that a member is let through, is asserted
        # on the listing below: this route's response schema walks relations,
        # which the test client cannot serialise from an async context.

    async def test_the_listing_no_longer_hands_out_everybody_s_evaluations(self):
        from aisc_backend.routers.evaluation import router as evaluation_router

        evaluations = TestAsyncClient(evaluation_router)
        with self.as_role(None):
            stranger = await evaluations.get("?status=Pending", headers=self.headers())
        with self.as_role("viewer"):
            member = await evaluations.get("?status=Pending", headers=self.headers())
        self.assertEqual(stranger.json(), [])
        self.assertEqual(len(member.json()), 1)

    async def test_a_stored_file_is_not_downloadable_by_its_object_name(self):
        """The names are uuids, but the listings handed them out."""
        from aisc_backend.routers.file import router as file_router

        files = TestAsyncClient(file_router)
        with self.as_role(None):
            refused = await files.get("/dataset/stored-object-name.csv", headers=self.headers())
        self.assertEqual(refused.status_code, 404)

    async def test_a_component_is_not_readable_by_id_alone(self):
        from aisc_backend.routers.component import router as component_router

        components = TestAsyncClient(component_router)
        with self.as_role(None):
            data = await components.get(f"/{self.dataset.pid}/data", headers=self.headers())
            row = await components.get(f"/{self.dataset.pid}", headers=self.headers())
        self.assertEqual(data.status_code, 404)
        self.assertEqual(row.status_code, 404)

    async def test_a_component_is_not_changeable_by_id_alone(self):
        from aisc_backend.routers.component import router as component_router

        components = TestAsyncClient(component_router)
        with self.as_role(None):
            renamed = await components.patch(
                f"/{self.dataset.pid}", json={"name": "mine now"}, headers=self.headers())
            deleted = await components.delete(f"/{self.dataset.pid}", headers=self.headers())
        self.assertEqual(renamed.status_code, 404)
        self.assertEqual(deleted.status_code, 404)

    async def test_a_viewer_may_not_change_a_component(self):
        from aisc_backend.routers.component import router as component_router

        components = TestAsyncClient(component_router)
        with self.as_role("viewer"):
            renamed = await components.patch(
                f"/{self.dataset.pid}", json={"name": "mine now"}, headers=self.headers())
        self.assertEqual(renamed.status_code, 403)

    async def test_a_project_s_configs_are_not_readable_by_a_stranger(self):
        from aisc_backend.routers.project_config import router as config_router

        configs = TestAsyncClient(config_router)
        with self.as_role(None):
            listing = await configs.get(f"/{self.project.pid}", headers=self.headers())
            changed = await configs.patch(
                f"/{self.project.pid}/{self.config.pid}", json={"json_value": {"type": "number", "value": 1}},
                headers=self.headers())
        self.assertEqual(listing.status_code, 404)
        self.assertEqual(changed.status_code, 404)

    async def test_a_viewer_may_read_configs_but_not_change_them(self):
        from aisc_backend.routers.project_config import router as config_router

        configs = TestAsyncClient(config_router)
        with self.as_role("viewer"):
            listing = await configs.get(f"/{self.project.pid}", headers=self.headers())
            changed = await configs.patch(
                f"/{self.project.pid}/{self.config.pid}", json={"json_value": {"type": "number", "value": 1}},
                headers=self.headers())
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(changed.status_code, 403)
