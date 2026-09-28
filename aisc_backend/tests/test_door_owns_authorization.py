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
