"""A project belongs to the people in it, here too.

The engine's project row is this service's side of a platform project. Who may
see it, and who may change it, is therefore the platform's answer, read from
`core.project_member` in the platform database rather than decided again here.

The door asks, not the routes: the upstream routers
ask nothing themselves. So these call the API through the middleware (DoorCase: auth on, the
membership lookup stubbed, no database). A refusal is the door's answer; "opens" means
every check passed and the door went on to open the header's project database. That a
member then reads her object, and that an object of another project is 404, is on
Postgres (test_isolation_engine_db: CONTROLS and NothingOfAIsReachableUnderB).
"""
import json
import unittest.mock as mock
import uuid
from types import SimpleNamespace

from django.db import DatabaseError

from aisc_backend.tests.isolation_support import configurator_only
from aisc_backend.tests.test_isolation_engine import A_PID, ISSUER, DoorCase, PlatformStub

PLATFORM_PROJECT = A_PID
OPENS = "opens"


class _AtTheDoor(DoorCase):
    def answer(self, method, path, role, body=None, roles=("primary-user",)):
        """The door's status code, or OPENS when it let the call through to the database."""
        from aisc_backend import projectdb

        opened = []

        def spy(pid):
            opened.append(pid)
            raise DatabaseError("stop here: the door opened the database")

        headers = {"X-AISC-Project": PLATFORM_PROJECT, "Authorization": self.bearer(roles)}
        with self.as_role(role), mock.patch.object(projectdb, "alias_for", side_effect=spy):
            response = self.call(method, path, headers, body)
        return OPENS if opened else response.status_code


@configurator_only
class ProjectMembershipTestCase(_AtTheDoor):
    PROJECT = str(uuid.uuid4())

    def test_a_stranger_gets_no_list_at_all(self):
        self.assertEqual(self.answer("GET", "/api/v1/projects", None), 404)

    def test_a_member_does(self):
        self.assertEqual(self.answer("GET", "/api/v1/projects", "viewer"), OPENS)

    def test_a_stranger_gets_404_on_the_project_itself(self):
        self.assertEqual(self.answer("GET", f"/api/v1/projects/{self.PROJECT}", None), 404)

    def test_a_member_can_read_it(self):
        self.assertEqual(self.answer("GET", f"/api/v1/projects/{self.PROJECT}", "viewer"), OPENS)

    def test_a_viewer_cannot_rename_it(self):
        self.assertEqual(self.answer("PATCH", f"/api/v1/projects/{self.PROJECT}", "viewer", {"name": "x"}), 403)

    def test_an_editor_can(self):
        self.assertEqual(self.answer("PATCH", f"/api/v1/projects/{self.PROJECT}", "editor", {"name": "x"}), OPENS)

    def test_entering_the_engine_inside_a_project_takes_an_editor(self):
        """The first visit makes the engine's row for the platform project,
        which is work on that project, not a way into it."""
        path = f"/api/v1/projects/for-platform/{PLATFORM_PROJECT}"
        self.assertEqual(self.answer("POST", path, None), 404)
        self.assertEqual(self.answer("POST", path, "viewer"), 403)
        self.assertEqual(self.answer("POST", path, "editor"), OPENS)

    def test_an_admin_is_in_every_project(self):
        self.assertEqual(self.answer("GET", f"/api/v1/projects/{self.PROJECT}", None, roles=("admin",)), OPENS)


@configurator_only
class TheSharedRuleTestCase(DoorCase):
    """Who may do what is aisc_identity.access's answer, the rule every other service applies,
    and its refusals read the same everywhere."""

    PATH = f"/api/v1/projects/{uuid.uuid4()}"

    def refusal(self, method, stub, roles=("primary-user",), authorization=None):
        headers = {"X-AISC-Project": PLATFORM_PROJECT, "Authorization": authorization or self.bearer(roles)}
        with stub:
            response = self.call(method, self.PATH, headers, {"name": "x"} if method == "PATCH" else None)
        return response.status_code, json.loads(response.content)["detail"]

    def test_the_door_refuses_in_the_shared_words(self):
        self.assertEqual(self.refusal("GET", self.as_role(None)), (404, "No such project."))
        self.assertEqual(self.refusal("PATCH", self.as_role("viewer")),
                         (403, "You can read this project but not change it."))
        down = mock.patch("aisc_backend.auth.membership._platform", return_value=PlatformStub(down=True))
        self.assertEqual(self.refusal("GET", down), (503, "Who may be here cannot be established just now."))

    def test_a_token_that_names_nobody_is_not_signed_in(self):
        import datetime

        import jwt

        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode({"iss": ISSUER, "azp": "aisc-gateway", "iat": now, "exp": now + datetime.timedelta(minutes=5),
                            "realm_access": {"roles": ["admin"]}}, self.private_key, algorithm="RS256")
        self.assertEqual(self.refusal("GET", self.as_role("owner"), authorization=f"Bearer {token}"),
                         (401, "sign in"))

    def require(self, claims, needed, stub):
        from aisc_backend.auth import membership

        with stub:
            return membership.require(SimpleNamespace(aisc_claims=claims), PLATFORM_PROJECT, needed)

    def test_require_asks_the_same_rule(self):
        from ninja.errors import HttpError

        admin = {"sub": "root", "realm_access": {"roles": ["admin"]}}
        member = {"sub": "alice", "realm_access": {"roles": ["primary-user"]}}
        self.assertEqual(self.require(admin, "editor", self.as_role(None)), "owner")
        self.assertEqual(self.require(member, "editor", self.as_role("editor")), "editor")
        for claims, needed, stub, expected in (
                (admin, "viewer", self.as_role(None, exists=False), (404, "No such project.")),
                (member, "viewer", self.as_role(None), (404, "No such project.")),
                (member, "editor", self.as_role("viewer"), (403, "You can read this project but not change it."))):
            with self.subTest(needed=needed, expected=expected):
                with self.assertRaises(HttpError) as refused:
                    self.require(claims, needed, stub)
                self.assertEqual((refused.exception.status_code, refused.exception.message), expected)


@configurator_only
class RunningTestsTakesAnEditorTestCase(_AtTheDoor):
    """Running an evaluation is the engine's main act of work on a project."""

    def _run(self, role):
        return self.answer("POST", "/api/v1/evaluations/task", role,
                           {"project_pid": str(uuid.uuid4()), "plugins_to_run": []})

    def test_a_stranger_is_told_there_is_no_such_project(self):
        self.assertEqual(self._run(None), 404)

    def test_a_viewer_may_not_run_tests(self):
        self.assertEqual(self._run("viewer"), 403)

    def test_an_editor_may(self):
        self.assertEqual(self._run("editor"), OPENS)


@configurator_only
class ReachingAProjectByAChildObjectsIdTestCase(_AtTheDoor):
    """A stranger is refused by an id that is not theirs, and the member it belongs to is not."""

    EVALUATION, COMPONENT, CONFIG, PROJECT = (str(uuid.uuid4()) for _ in range(4))

    def test_an_evaluation_is_not_readable_by_id_alone(self):
        self.assertEqual(self.answer("GET", f"/api/v1/evaluations/{self.EVALUATION}", None), 404)

    def test_the_listing_no_longer_hands_out_everybody_s_evaluations(self):
        self.assertEqual(self.answer("GET", "/api/v1/evaluations?status=Pending", None), 404)
        self.assertEqual(self.answer("GET", "/api/v1/evaluations?status=Pending", "viewer"), OPENS)

    def test_a_stored_file_is_not_downloadable_by_its_object_name(self):
        """The names are uuids, but the listings handed them out."""
        self.assertEqual(self.answer("GET", "/api/v1/files/dataset/stored-object-name.csv", None), 404)

    def test_a_component_is_not_readable_by_id_alone(self):
        self.assertEqual(self.answer("GET", f"/api/v1/components/{self.COMPONENT}/data", None), 404)
        self.assertEqual(self.answer("GET", f"/api/v1/components/{self.COMPONENT}", None), 404)

    def test_a_component_is_not_changeable_by_id_alone(self):
        self.assertEqual(self.answer("PATCH", f"/api/v1/components/{self.COMPONENT}", None, {"name": "x"}), 404)
        self.assertEqual(self.answer("DELETE", f"/api/v1/components/{self.COMPONENT}", None), 404)

    def test_a_viewer_may_not_change_a_component(self):
        self.assertEqual(self.answer("PATCH", f"/api/v1/components/{self.COMPONENT}", "viewer", {"name": "x"}), 403)

    def test_a_project_s_configs_are_not_readable_by_a_stranger(self):
        body = {"json_value": {"type": "number", "value": 1}}
        self.assertEqual(self.answer("GET", f"/api/v1/project/settings/{self.PROJECT}", None), 404)
        self.assertEqual(self.answer("PATCH", f"/api/v1/project/settings/{self.PROJECT}/{self.CONFIG}", None, body),
                         404)

    def test_a_viewer_may_read_configs_but_not_change_them(self):
        body = {"json_value": {"type": "number", "value": 1}}
        self.assertEqual(self.answer("GET", f"/api/v1/project/settings/{self.PROJECT}", "viewer"), OPENS)
        self.assertEqual(self.answer("PATCH", f"/api/v1/project/settings/{self.PROJECT}/{self.CONFIG}", "viewer",
                                     body), 403)
