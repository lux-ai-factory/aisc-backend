"""Isolation 2026-09-25, the engine without a database of its own (01-specs.md section 7).

After isolation the engine keeps no data in `platform`: every ORM read, write
and migration goes to the database of the platform project the request was
admitted for (`project_<hex>`, schema `engine`), and `platform` is read only for
membership and the project's name. These tests need no database: they read the
settings a deployed engine would have (in a child process, see
isolation_support.settings_probe), the migration graph, the source, and drive
the door with the membership lookup stubbed. The Postgres half is
test_isolation_engine_db.py.

Interface pinned here (decided in stage 2, 02-tests.md, because the spec names
the module but not its functions):

- `aisc_backend.projectdb.ProjectDatabaseRouter`: the only entry of
  settings.DATABASE_ROUTERS when DB_ENGINE is Postgres;
- `aisc_backend.projectdb.admitted`: the ContextVar holding the alias admitted
  for the current request (None outside one);
- `aisc_backend.projectdb.alias_for(platform_pid) -> str`: validates the pid,
  registers the alias on first use and returns its name, which is the database
  name `project_<hex>`;
- `run ticket` = hex HMAC-SHA256 over `platform_pid + "." + evaluation_pid` with
  settings.SECRET_KEY (DJANGO_SECRET_KEY), sent as `X-AISC-Run`;
- gap E-G1: every internal call also sends `X-AISC-Evaluation: <evaluation pid>`
  (three internal routes have no evaluation in their path); the door checks the
  ticket against it and, when the path names an evaluation, requires both equal;
- the Celery message of the start route is
  `aisc_eval.celery_tasks.run_evaluation` with args
  `[platform_pid, evaluation_pid, ticket]`. How the backend builds it is left to
  stage 4 (routers/evaluation.py is a frozen Sean file, I7.12), so these tests
  read the message, not the router's source.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import re
import unittest.mock as mock
import uuid
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.loader import MigrationLoader
from django.test import AsyncClient, SimpleTestCase, TestCase

from aisc_backend.tests.isolation_support import BACKEND, EXAMPLE_DB, EXAMPLE_PID, settings_probe

MIGRATIONS = BACKEND / "aisc_backend" / "migrations"
NEW_LEAF = ("aisc_backend", "0025_the_database_is_the_project")

#: I7.7: these stay byte for byte as they are (their core blocks are already
#: guarded by to_regclass). Hashes taken on isolation/2026-09-25 at 94f23e0.
UNCHANGED_MIGRATIONS = {
    "0015_evaluation_system_id_project_platform_project_id.py":
        "88b6debb260d03db52a4bb1defb7c9397744cc45e4b222e305d1235d7778c724",
    "0022_parts_belong_to_a_version_of_the_one_system.py":
        "4d6ad4a8ab76b74abf71cd0c54c3e6f1c8dd9ce0b4d8504d0077c8869a211bc1",
    "0023_one_system_per_project_again.py":
        "35233b16b7932a6add5f619079cc87a180f4505fb0e26626be40fd1791f9236f",
}


def ticket(platform_pid, evaluation_pid, key=None) -> str:
    """I7.3: what the backend mints and the door checks."""
    key = settings.SECRET_KEY if key is None else key
    return hmac.new(key.encode(), f"{platform_pid}.{evaluation_pid}".encode(), hashlib.sha256).hexdigest()


def _projectdb(test):
    try:
        return importlib.import_module("aisc_backend.projectdb")
    except ImportError as exc:
        test.fail(f"I7.1: aisc_backend/projectdb.py (router, ContextVar, aliases) is missing: {exc}")


# ── I7.7, I7.12: the migration that makes the evaluation point at project.system ──


class TheNewMigration(SimpleTestCase):
    def _file(self):
        found = sorted(MIGRATIONS.glob("0025_*.py"))
        if not found:
            self.fail("I7.7: aisc_backend/migrations/0025_the_database_is_the_project.py is missing")
        return found[0]

    def test_i7_7_0025_has_the_name_of_the_spec(self):
        self.assertEqual(self._file().name, "0025_the_database_is_the_project.py")

    def test_i7_7_0025_adds_the_fk_to_project_system_only_when_it_exists(self):
        text = self._file().read_text()
        self.assertIn("RunPython", text, "I7.7: 0025 is a RunPython migration")
        self.assertIn("aisc_backend_evaluation_system_id_fkey", text)
        self.assertRegex(text, r"to_regclass\(\s*'project\.system'\s*\)")
        self.assertRegex(text, r"REFERENCES\s+project\.system\s*\(\s*pid\s*\)\s+ON DELETE SET NULL")
        self.assertIn("postgresql", text, "I7.7: Postgres only, a no-op on sqlite")
        self.assertNotRegex(text, r"core\.", "I7.7: 0025 names nothing in core")

    def test_i7_7_0015_0022_0023_are_unchanged(self):
        for name, digest in UNCHANGED_MIGRATIONS.items():
            with self.subTest(migration=name):
                self.assertEqual(hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest(), digest)

    def test_i7_12_the_leaf_becomes_0025(self):
        """The leaf-0024 assertion of test_one_system_per_project.py
        (test_s1_0023_is_the_leaf) changes in WP E1 to this one."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        self.assertEqual(loader.graph.leaf_nodes("aisc_backend"), [NEW_LEAF])
        parents = loader.graph.node_map[NEW_LEAF].parents
        self.assertEqual({p.key for p in parents}, {("aisc_backend", "0024_no_login_of_its_own")})


class TheNewMigrationOnSqlite(TestCase):
    def test_i7_7_0025_is_applied_as_a_no_op_on_sqlite(self):
        if connection.vendor != "sqlite":
            self.skipTest("about sqlite")
        loader = MigrationLoader(connection)
        self.assertIn(NEW_LEAF, loader.applied_migrations,
                      "I7.7: 0025 must exist and apply on sqlite (a no-op there)")


# ── I7.1: settings, router, aliases ──


class SettingsOfADeployedEngine(SimpleTestCase):
    """What config.settings says when DB_ENGINE is Postgres (read in a child process)."""

    def probe(self, code):
        result = settings_probe(code)
        if not result["ok"]:
            self.fail(f"I7.1: the probe failed: {result['error']}")
        return result["out"]

    def test_i7_1_default_is_the_dummy_backend(self):
        engine = self.probe("from django.conf import settings\nout = settings.DATABASES['default']['ENGINE']")
        self.assertEqual(engine, "django.db.backends.dummy")

    def test_i7_1_the_platform_alias_is_core_only(self):
        alias = self.probe("from django.conf import settings\nout = settings.DATABASES.get('platform')")
        self.assertIsNotNone(alias, "I7.1: an alias 'platform' for membership and names")
        self.assertEqual(alias["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(alias["NAME"], "platform")
        self.assertIn("search_path=core", alias.get("OPTIONS", {}).get("options", ""))
        self.assertNotIn("engine", alias.get("OPTIONS", {}).get("options", ""))

    def test_i7_1_the_router_is_the_project_router(self):
        routers = self.probe("from django.conf import settings\nout = list(settings.DATABASE_ROUTERS)")
        self.assertEqual(routers, ["aisc_backend.projectdb.ProjectDatabaseRouter"])

    def test_i7_1_a_query_outside_an_admitted_request_fails(self):
        result = settings_probe(
            "from aisc_backend.models import Project\nout = Project.objects.count()")
        self.assertFalse(result["ok"], "I7.1: an ORM query with no admitted project must fail")
        self.assertNotIn("OperationalError", result["error"],
                         "I7.1: it must fail before connecting anywhere, not by failing to connect")

    def test_i7_1_i17_1_an_alias_per_project_database(self):
        out = self.probe(
            "from aisc_backend import projectdb\n"
            "from django.db import connections\n"
            f"alias = projectdb.alias_for('{EXAMPLE_PID.upper()}')\n"
            "s = connections.settings[alias]\n"
            "out = {'alias': alias, 'NAME': s['NAME'], 'OPTIONS': s.get('OPTIONS', {}),"
            " 'CONN_MAX_AGE': s.get('CONN_MAX_AGE'), 'USER': s.get('USER'), 'ENGINE': s['ENGINE']}")
        self.assertEqual(out["NAME"], EXAMPLE_DB, "I1.8: the one naming rule")
        self.assertEqual(out["alias"], EXAMPLE_DB)
        self.assertEqual(out["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(out["USER"], "engine_rw")
        self.assertIn("-c search_path=engine", out["OPTIONS"].get("options", ""))
        self.assertNotIn("core", out["OPTIONS"].get("options", ""))
        self.assertEqual(out["CONN_MAX_AGE"], 0, "I7.1, I17.1: no persistent connection per project")

    def test_i7_1_i1_8_only_a_pid_names_an_alias(self):
        for bad in ("", "abc", "../platform", EXAMPLE_PID + "x", "platform", "default"):
            with self.subTest(bad=bad):
                result = settings_probe(
                    f"from aisc_backend import projectdb\nout = projectdb.alias_for({bad!r})")
                self.assertFalse(result["ok"], f"I1.8: {bad!r} must be refused")
                self.assertNotIn("ModuleNotFoundError", result["error"],
                                 "I7.1: aisc_backend/projectdb.py is missing")


class TheRouter(SimpleTestCase):
    def router(self):
        projectdb = _projectdb(self)
        if not hasattr(projectdb, "ProjectDatabaseRouter") or not hasattr(projectdb, "admitted"):
            self.fail("I7.1: projectdb.ProjectDatabaseRouter and projectdb.admitted are the interface")
        return projectdb, projectdb.ProjectDatabaseRouter()

    def test_i7_1_reads_and_writes_go_to_the_admitted_alias(self):
        from aisc_backend.models import Project
        from aisc_backend.models.evaluation import Evaluation

        projectdb, router = self.router()
        token = projectdb.admitted.set(EXAMPLE_DB)
        try:
            for model in (Project, Evaluation):
                self.assertEqual(router.db_for_read(model), EXAMPLE_DB)
                self.assertEqual(router.db_for_write(model), EXAMPLE_DB)
        finally:
            projectdb.admitted.reset(token)

    def test_i7_1_nothing_admitted_routes_nowhere(self):
        from aisc_backend.models import Project

        projectdb, router = self.router()
        self.assertIsNone(projectdb.admitted.get(None))
        for pick in (router.db_for_read, router.db_for_write):
            try:
                chosen = pick(Project)
            except Exception:
                continue  # refusing is right
            self.assertNotIn(chosen, (None, "default", "platform"),
                             "I7.1: with nothing admitted a model is never routed to default or platform")

    def test_i7_1_never_migrates_platform_or_default(self):
        _, router = self.router()
        self.assertIs(router.allow_migrate("platform", "aisc_backend"), False)
        self.assertIs(router.allow_migrate("default", "aisc_backend"), False)
        self.assertIs(router.allow_migrate(EXAMPLE_DB, "aisc_backend"), True)

    def test_i7_1_no_relation_across_databases(self):
        from aisc_backend.models import Project

        _, router = self.router()
        a, b = Project(name="a"), Project(name="b")
        a._state.db, b._state.db = EXAMPLE_DB, "project_" + "0" * 32
        self.assertIs(router.allow_relation(a, b), False)
        b._state.db = EXAMPLE_DB
        self.assertIsNot(router.allow_relation(a, b), False)

    def test_i7_1_the_admitted_alias_crosses_sync_to_async(self):
        from asgiref.sync import sync_to_async
        from aisc_backend.models import Project

        projectdb, router = self.router()

        async def request():
            token = projectdb.admitted.set(EXAMPLE_DB)
            try:
                return await sync_to_async(router.db_for_read)(Project)
            finally:
                projectdb.admitted.reset(token)

        self.assertEqual(asyncio.run(request()), EXAMPLE_DB)


class OnlyTwoFilesReadPlatform(SimpleTestCase):
    """I7.1: raw SQL on core.* only in auth/membership.py and platform_projects.py,
    both on the `platform` alias; I7.8: the stamp reads project.system."""

    ALLOWED = {"aisc_backend/auth/membership.py", "aisc_backend/platform_projects.py"}

    def sources(self):
        root = BACKEND / "aisc_backend"
        for path in root.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            yield rel, path.read_text()

    #: SQL that reads or writes core, not a comment that names it (models/project.py
    #: and models/evaluation.py mention core.* in comments and are frozen, I7.12).
    SQL_ON_CORE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|to_regclass\(\s*')\s*core\.", re.IGNORECASE)

    def test_i7_1_core_is_named_only_by_membership_and_the_name_lookup(self):
        naming = sorted(rel for rel, text in self.sources() if self.SQL_ON_CORE.search(text))
        self.assertLessEqual(set(naming), self.ALLOWED, naming)

    def test_i7_1_those_two_use_the_platform_alias(self):
        for rel in sorted(self.ALLOWED):
            with self.subTest(file=rel):
                text = (BACKEND / rel).read_text()
                self.assertRegex(text, r"connections\[\s*['\"]platform['\"]\s*\]")
                self.assertNotRegex(text, r"from django\.db import[^\n]*\bconnection\b(?!s)")

    def test_i7_8_latest_reads_project_system(self):
        from aisc_backend.repositories import system_version_repository

        self.assertEqual(system_version_repository.LATEST,
                         "SELECT pid FROM project.system ORDER BY number DESC LIMIT 1")


class TheOneShotMigrates(SimpleTestCase):
    def test_i7_6_migrate_projects_is_a_command(self):
        from django.core.management import get_commands

        self.assertEqual(get_commands().get("migrate_projects"), "aisc_backend",
                         "I7.6: manage.py migrate_projects")

    def test_i7_6_it_takes_an_advisory_lock_per_database(self):
        path = BACKEND / "aisc_backend" / "management" / "commands" / "migrate_projects.py"
        if not path.exists():
            self.fail("I7.6: aisc_backend/management/commands/migrate_projects.py is missing")
        self.assertRegex(path.read_text(), r"pg_advisory(_xact)?_lock")

    def test_i7_6_the_image_no_longer_migrates_platform(self):
        text = (BACKEND / "Dockerfile").read_text()
        self.assertNotRegex(text, r"manage\.py migrate(?!_projects)\b",
                            "I7.6: aisc-backend no longer runs manage.py migrate on platform")


# ── I7.2, I7.3: the door ──


ISSUER = "http://keycloak:8080/realms/aisc"
A_PID = "11111111-1111-4111-8111-111111111111"


class _FakeKey:
    def __init__(self, key):
        self.key = key


class DoorCase(SimpleTestCase):
    """The API through Django's middleware, auth on, membership stubbed.

    SimpleTestCase: no database may be touched, so a request that reaches a
    query before the door has refused shows up as a failure, not a pass.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from cryptography.hazmat.primitives.asymmetric import rsa

        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        from aisc_backend.auth import keycloak

        fake_client = mock.Mock(
            get_signing_key_from_jwt=mock.Mock(return_value=_FakeKey(self.private_key.public_key())))
        for patcher in (
            mock.patch.object(keycloak, "AUTH_ENABLED", True),
            mock.patch.object(keycloak, "KEYCLOAK_ISSUER", ISSUER),
            mock.patch.object(keycloak, "_get_jwks_client", return_value=fake_client),
            mock.patch.dict("os.environ", {"INTERNAL_API_KEY": "the-internal-key"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        # ASGI, as deployed: LoggingNinjaAPI reads request.scope, which the
        # WSGI test client does not have (it would answer 500 for everything).
        self.client = AsyncClient(raise_request_exception=False)

    def bearer(self, roles=("primary-user",), subject="alice"):
        import datetime

        import jwt

        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode({"sub": subject, "preferred_username": subject, "iss": ISSUER, "iat": now,
                            "exp": now + datetime.timedelta(minutes=5),
                            "realm_access": {"roles": list(roles)}},
                           self.private_key, algorithm="RS256")
        return f"Bearer {token}"

    def call(self, method, path, headers, body=None):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs.update(data=body, content_type="application/json")
        async def go():
            return await getattr(self.client, method.lower())(path, **kwargs)

        return asyncio.run(go())

    def as_role(self, role):
        return mock.patch("aisc_backend.auth.membership.role_in_project", return_value=role)


class TheDoorOrder(DoorCase):
    """I7.2 as one table, in the spec's order: each row names what the caller
    sends and what the door answers. A later check never runs before an earlier
    one (e.g. a non-pid header is 404 even without a token, a stranger writing is
    404 not 403). "opens" means every check passed and the door then, and only
    then, opened the header's project database (projectdb.alias_for).

    The row "a pid with no core.project row is 404" needs the platform database
    and is in test_isolation_engine_db.py (TheDoorOnPostgres)."""

    PATH = f"/api/v1/projects/{uuid.uuid4()}"
    READ_POST = f"/api/v1/evaluations/{uuid.uuid4()}/measurements/metric-names"
    DELETE_PATH = f"/api/v1/components/{uuid.uuid4()}"
    UNREADABLE = "unreadable"

    #: (row, method, path, project header or None, token role or None, membership, expected)
    TABLE = [
        ("no header, no token", "GET", "PATH", None, None, "owner", 400),
        ("no header, a token", "GET", "PATH", None, "primary-user", "owner", 400),
        ("not a pid, no token", "GET", "PATH", "abc", None, "owner", 404),
        ("not a pid: a path", "GET", "PATH", "../platform", "primary-user", "owner", 404),
        ("not a pid: a database name", "GET", "PATH", "project_" + "0" * 32, "primary-user", "owner", 404),
        ("not a pid: a pid and more", "GET", "PATH", A_PID + "x", "primary-user", "owner", 404),
        ("a pid, no token", "GET", "PATH", A_PID, None, "owner", 401),
        ("membership unreadable", "GET", "PATH", A_PID, "primary-user", UNREADABLE, 503),
        ("a stranger reading", "GET", "PATH", A_PID, "primary-user", None, 404),
        ("a stranger writing", "PATCH", "PATH", A_PID, "primary-user", None, 404),
        ("a viewer writing", "PATCH", "PATH", A_PID, "primary-user", "viewer", 403),
        ("a viewer deleting", "DELETE", "DELETE_PATH", A_PID, "primary-user", "viewer", 403),
        ("a viewer reading", "GET", "PATH", A_PID, "primary-user", "viewer", "opens"),
        ("a viewer's read POST (READ_POSTS)", "POST", "READ_POST", A_PID, "primary-user", "viewer", "opens"),
        ("an editor writing", "PATCH", "PATH", A_PID, "primary-user", "editor", "opens"),
        ("an admin, not a member", "GET", "PATH", A_PID, "admin", None, "opens"),
        ("the header in upper case", "GET", "PATH", A_PID.upper(), "primary-user", "owner", "opens"),
    ]

    def test_i7_2_the_door_order(self):
        try:
            projectdb = importlib.import_module("aisc_backend.projectdb")
        except ImportError:
            projectdb = None
        for row, method, path, header, token, member, expected in self.TABLE:
            with self.subTest(row=row):
                headers = {}
                if header is not None:
                    headers["X-AISC-Project"] = header
                if token is not None:
                    headers["Authorization"] = self.bearer(roles=(token,))
                if member == self.UNREADABLE:
                    stub = mock.patch("aisc_backend.auth.membership.role_in_project",
                                      side_effect=DatabaseError("simulated: platform is down"))
                else:
                    stub = self.as_role(member)
                opened = []

                def spy(pid):
                    opened.append(str(pid).lower())
                    raise DatabaseError("stop here: the door opened the database")

                body = {"name": "renamed"} if method in ("PATCH", "POST") else None
                with stub:
                    if projectdb is not None and hasattr(projectdb, "alias_for"):
                        with mock.patch.object(projectdb, "alias_for", side_effect=spy):
                            response = self.call(method, getattr(self, path), headers, body)
                    else:
                        response = self.call(method, getattr(self, path), headers, body)
                if expected == "opens":
                    self.assertIsNotNone(projectdb, "I7.1: aisc_backend/projectdb.py is missing")
                    self.assertEqual(opened, [A_PID], f"I7.2 {row}: the door must open {A_PID}'s database")
                else:
                    self.assertEqual(response.status_code, expected, f"I7.2 {row}")
                    self.assertEqual(opened, [], f"I7.2 {row}: a refused call opens no database")


class TheDoorsExemptions(DoorCase):
    """I7.2: the unauthenticated and project-less routes need no header."""

    def test_i7_2_docs_and_openapi_need_nothing(self):
        for path in ("/api/docs", "/api/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path, {}).status_code, 200)

    def test_i7_2_project_less_routes_need_no_header(self):
        for path in ("/api/v1/app/app-name", "/api/v1/me", "/api/v1/me/admin", "/api/v1/audit",
                     "/api/v1/plugins"):
            with self.subTest(path=path):
                response = self.call("GET", path, {"Authorization": self.bearer(roles=("admin",))})
                self.assertNotEqual(response.status_code, 400, f"I7.2: {path} is exempt")

    def test_i7_2_i7_5_installing_a_plugin_needs_the_header(self):
        response = self.call("POST", "/api/v1/plugins", {"Authorization": self.bearer(roles=("admin",))},
                             body={"package_name": "demo", "version": "0.1", "project_uuid": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 400)


class TheWorkersTicket(DoorCase):
    """I7.3: an internal call needs the shared secret (401) and a run ticket
    bound to that project and that evaluation (403)."""

    def internal(self, evaluation, project=A_PID, run=None, secret="the-internal-key", named=None, path=None):
        headers = {"X-Internal-Secret": secret, "X-AISC-Project": project,
                   "X-AISC-Evaluation": str(evaluation if named is None else named)}
        if run is not None:
            headers["X-AISC-Run"] = run
        return self.call("GET", path or f"/api/v1/internal/evaluations/{evaluation}", headers)

    def test_i7_3_wrong_secret_is_401(self):
        evaluation = uuid.uuid4()
        self.assertEqual(self.internal(evaluation, run=ticket(A_PID, evaluation), secret="wrong").status_code, 401)

    def test_i7_3_no_ticket_is_403(self):
        self.assertEqual(self.internal(uuid.uuid4()).status_code, 403)

    def test_i7_3_a_ticket_of_another_evaluation_is_403(self):
        self.assertEqual(self.internal(uuid.uuid4(), run=ticket(A_PID, uuid.uuid4())).status_code, 403)

    def test_i7_3_a_ticket_of_another_project_is_403(self):
        evaluation = uuid.uuid4()
        other = str(uuid.uuid4())
        self.assertEqual(self.internal(evaluation, run=ticket(other, evaluation)).status_code, 403)

    def test_i7_3_a_forged_ticket_is_403(self):
        evaluation = uuid.uuid4()
        self.assertEqual(self.internal(evaluation, run=ticket(A_PID, evaluation, key="not-the-key")).status_code, 403)

    def test_i7_3_e_g1_the_path_and_the_named_evaluation_must_agree(self):
        evaluation, other = uuid.uuid4(), uuid.uuid4()
        response = self.internal(evaluation, run=ticket(A_PID, other), named=other)
        self.assertEqual(response.status_code, 403, "E-G1: a ticket for another evaluation named in the header")

    def test_i7_3_e_g1_a_route_without_an_evaluation_needs_the_named_one(self):
        evaluation = uuid.uuid4()
        for path in (f"/api/v1/internal/projects/settings/{uuid.uuid4()}",
                     "/api/v1/internal/files/dataset/some-file.csv",
                     "/api/v1/internal/files/model/some-model.onnx"):
            with self.subTest(path=path):
                headers = {"X-Internal-Secret": "the-internal-key", "X-AISC-Project": A_PID,
                           "X-AISC-Run": ticket(A_PID, evaluation)}
                self.assertEqual(self.call("GET", path, headers).status_code, 403,
                                 "E-G1: no X-AISC-Evaluation, nothing to bind the ticket to")
                headers["X-AISC-Evaluation"] = str(uuid.uuid4())
                self.assertEqual(self.call("GET", path, headers).status_code, 403,
                                 "E-G1: the ticket is not for the evaluation named")

    def test_i7_3_no_project_header_is_400(self):
        evaluation = uuid.uuid4()
        response = self.call("GET", f"/api/v1/internal/evaluations/{evaluation}",
                             {"X-Internal-Secret": "the-internal-key", "X-AISC-Run": ticket(A_PID, evaluation),
                              "X-AISC-Evaluation": str(evaluation)})
        self.assertEqual(response.status_code, 400)


class TheStartRouteMintsTheTicket(TestCase):
    """I7.3 through the start route, on the test runner's database (as test_evaluation_system_stamp)."""

    async def test_i7_3_an_editor_starting_a_run_dispatches_the_ticket(self):
        from ninja.testing import TestAsyncClient

        from aisc_backend.auth import keycloak
        from aisc_backend.models import Plugin, PluginConfig, Project, ProjectStatus
        from aisc_backend.routers.evaluation import router as evaluation_router

        project = await Project.objects.acreate(name="MCAS", status=ProjectStatus.Created,
                                                platform_project_id=A_PID)
        plugin = await Plugin.objects.acreate(name="Demo", display_name="Demo", description="",
                                              package_name="demo", version="0.1", project=project,
                                              enabled=True)
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        plugin.current_config = config
        await plugin.asave()
        no_errors = {"missing": [], "invalid": [], "ambiguous": []}
        from aisc_backend.services import celery_service

        send = mock.Mock(return_value=mock.Mock(task_id=str(uuid.uuid4())))
        with mock.patch.object(keycloak, "AUTH_ENABLED", False), \
             mock.patch("aisc_backend.routers.evaluation.plugin_loader.load_plugin",
                        return_value=mock.Mock(project_config_definitions=[])), \
             mock.patch("aisc_backend.routers.evaluation.validate_plugin_settings",
                        new=mock.AsyncMock(return_value=no_errors)), \
             mock.patch.object(celery_service.celery, "send_task", new=send):
            response = await TestAsyncClient(evaluation_router).post(
                "/task", json={"project_pid": str(project.pid),
                               "plugins_to_run": [{"name": "Demo", "inputs": []}]},
                headers={"Authorization": "Bearer development"})
        self.assertEqual(response.status_code, 200, response.content)
        evaluation_pid = response.json()["pid"]
        send.assert_called_once()
        name = send.call_args.args[0] if send.call_args.args else send.call_args.kwargs.get("name")
        args = send.call_args.kwargs.get("args") or send.call_args.args[1]
        self.assertEqual(name, "aisc_eval.celery_tasks.run_evaluation")
        self.assertEqual([str(a) for a in args], [A_PID, str(evaluation_pid), ticket(A_PID, evaluation_pid)],
                         "I7.3: the task is run_evaluation(platform_pid, evaluation_pid, ticket)")
        self.assertNotRegex(repr(send.call_args), r"postgres|password|dbname",
                            "I7.3: a task never carries a DSN")
