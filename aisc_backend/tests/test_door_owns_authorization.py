"""The door holds every configurator rule (adapt plan 2026-09-28, item 2). DoorCase: auth on,
membership stubbed, no database; each refusal here happens before a database is opened."""
import json
import uuid
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from aisc_backend.tests.isolation_support import configurator_only
from aisc_backend.tests.test_isolation_engine import A_PID, DoorCase


@configurator_only
class TheDoorsOwnRules(DoorCase):
    def test_no_project_is_made_in_the_engine(self):
        with self.as_role("owner"):
            r = self.call("POST", "/api/v1/projects",
                          {"X-AISC-Project": A_PID, "Authorization": self.bearer(("admin",))}, {"name": "x"})
        self.assertEqual((r.status_code, json.loads(r.content)["detail"]),
                         (403, "projects are made on the Configurator's launcher"))

    def test_installing_removing_refreshing_take_admin(self):
        body = {"package_name": "p", "version": "1", "project_uuid": str(uuid.uuid4())}
        for method, path in (("POST", "/api/v1/plugins"), ("DELETE", "/api/v1/plugins"),
                             ("POST", "/api/v1/plugins/refresh")):
            with self.as_role("editor"):
                r = self.call(method, path, {"X-AISC-Project": A_PID, "Authorization": self.bearer()}, body)
            self.assertEqual((r.status_code, json.loads(r.content)["detail"]),
                             (403, "this needs the 'admin' role"), (method, path))

    def test_switching_a_plugin_on_or_off_takes_admin(self):
        """PATCH .../enabled flips the flag DELETE flips: an editor could otherwise switch back on a plugin an
        admin removed (code review 2026-10-06)."""
        path = f"/api/v1/plugins/{uuid.uuid4()}/enabled"
        with self.as_role("editor"):
            r = self.call("PATCH", path, {"X-AISC-Project": A_PID, "Authorization": self.bearer()}, {"enabled": True})
        self.assertEqual((r.status_code, json.loads(r.content)["detail"]), (403, "this needs the 'admin' role"))
        with self.as_role("editor"):
            r = self.call("PATCH", path, {"X-AISC-Project": A_PID, "Authorization": self.bearer(("admin",))},
                          {"enabled": True})
        self.assertNotEqual(r.status_code, 403, r.content)

    def test_a_stranger_gets_nothing_about_a_file_or_a_task(self):
        # pins today's door (strangers are 404 before any route); may pass before the change
        for path in ("/api/v1/files/dataset/x.csv", "/api/v1/files/artifact/y.zip",
                     f"/api/v1/tasks/{uuid.uuid4()}/status"):
            with self.as_role(None):
                r = self.call("GET", path, {"X-AISC-Project": A_PID, "Authorization": self.bearer()})
            self.assertEqual(r.status_code, 404, path)


@configurator_only
class TheGatewaySessionIsABearer(SimpleTestCase):
    def test_the_gateway_token_is_copied_when_no_authorization_is_sent(self):
        from aisc_backend import project_door
        request = RequestFactory().get("/api/v1/projects", HTTP_X_AUTH_REQUEST_ACCESS_TOKEN="tok")
        project_door.bearer_from_gateway(request)
        self.assertEqual(request.META["HTTP_AUTHORIZATION"], "Bearer tok")

    def test_an_authorization_sent_is_left_alone(self):
        from aisc_backend import project_door
        request = RequestFactory().get("/api/v1/projects", HTTP_AUTHORIZATION="Bearer mine",
                                       HTTP_X_AUTH_REQUEST_ACCESS_TOKEN="tok")
        project_door.bearer_from_gateway(request)
        self.assertEqual(request.META["HTTP_AUTHORIZATION"], "Bearer mine")


class SeansMissingRoleAnswer(DoorCase):
    """Item 3: a verified token that lacks the role is Sean's 401, in both modes."""
    def test_a_role_guarded_route_answers_401(self):
        from aisc_backend.auth.keycloak import require_role
        from ninja.testing import TestClient
        from ninja import Router
        router = Router()
        @router.get("/x", auth=require_role("admin"))
        def x(request):
            return {"ok": True}
        r = TestClient(router).get("/x", headers={"Authorization": self.bearer(("primary-user",))})
        self.assertEqual(r.status_code, 401)


class AComponentWithNoSystem(SimpleTestCase):
    """Final review M2: a stored file whose component has no AI system belongs to nobody: 404, not a 500."""
    def test_the_file_check_answers_404(self):
        from ninja.errors import HttpError

        from aisc_backend.auth import membership
        from aisc_backend.models.common import StorageContainer

        orphan = mock.Mock(system=None)
        rows = mock.Mock()
        rows.filter.return_value.select_related.return_value.first.return_value = orphan
        with mock.patch.object(membership, "enforced", return_value=True), \
                mock.patch("aisc_backend.models.AIComponent.objects", rows):
            with self.assertRaises(HttpError) as refused:
                membership.for_stored_file(object(), StorageContainer.Datasets, "x.csv")
        self.assertEqual(refused.exception.status_code, 404)
