"""Telling the dashboard about a project: a real call, with the token (ANA-01).

The pilot's rule is that the dashboard refuses every call whose bridge token is
empty — so our side never makes one. Everything here is checked at the process
boundary: a real HTTP server on a port stands in for the dashboard's bridge, and
the test reads what actually arrived (method, path, the token header, the body).
Two rules are asserted on both sides of that boundary:

* with a token, the call goes out and carries it;
* with no token (or no URL), **nothing is sent** and the caller is told False.

Best-effort is asserted too: a server that answers 500, or is not there at all,
never raises from `register`/`unregister` — only `sync_tool` raises, because a
person asked for that tile and has to be told.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import TestCase, override_settings

from aisc_backend.models.project import Project
from aisc_backend.services import dashboard_bridge

TOKEN = "the-bridge-token"


class _Recorder(BaseHTTPRequestHandler):
    """A stand-in for the dashboard's bridge: records what arrived, answers `status`."""

    received: list = []
    status = 200
    answer: dict = {"slug": "assessed", "charts": 2, "deleted": False}

    def _record(self, body: bytes) -> None:
        self.received.append(
            {
                "method": self.command,
                "path": self.path,
                "token": self.headers.get(dashboard_bridge.TOKEN_HEADER.lower()),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(body) if body else None,
            }
        )

    def _answer(self) -> None:
        if self.status >= 400:
            self.send_response(self.status)
            self.end_headers()
            return
        payload = json.dumps(self.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802 - http.server's naming
        length = int(self.headers.get("Content-Length") or 0)
        self._record(self.rfile.read(length))
        self._answer()

    def do_DELETE(self):  # noqa: N802
        self._record(b"")
        self._answer()

    def do_PUT(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self._record(self.rfile.read(length))
        self._answer()

    def log_message(self, *args):  # keep the test output clean
        return


class BridgeTestCase(TestCase):
    """A project and a live stand-in server for the dashboard's bridge."""

    def setUp(self):
        _Recorder.received = []
        _Recorder.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.project = Project.objects.create(name="Assessment Two", description="",
                                              status="Ready")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def received(self) -> list:
        return _Recorder.received


@override_settings(RESULTS_DASHBOARD_BRIDGE_URL="", RESULTS_DASHBOARD_BRIDGE_TOKEN="")
class ClosedBridgeTest(BridgeTestCase):

    def test_with_no_url_and_no_token_nothing_is_sent(self):
        self.assertFalse(dashboard_bridge.configured())
        self.assertFalse(dashboard_bridge.register(self.project))
        self.assertFalse(dashboard_bridge.unregister(self.project))
        self.assertEqual(self.received, [])

    @override_settings(RESULTS_DASHBOARD_BRIDGE_URL="http://127.0.0.1:9")
    def test_with_a_url_but_no_token_nothing_is_sent(self):
        """The dashboard refuses a call with an empty token, so none is made."""
        self.assertFalse(dashboard_bridge.configured())
        self.assertFalse(dashboard_bridge.register(self.project))
        self.assertEqual(self.received, [])
        with self.assertRaises(dashboard_bridge.BridgeClosed):
            dashboard_bridge.sync_tool(self.project, "fairness", "Fairness")


@override_settings(RESULTS_DASHBOARD_BRIDGE_TOKEN=TOKEN)
class OpenBridgeTest(BridgeTestCase):

    def setUp(self):
        super().setUp()
        self.override = override_settings(RESULTS_DASHBOARD_BRIDGE_URL=self.base)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_register_carries_the_token_and_the_project(self):
        self.assertTrue(dashboard_bridge.configured())
        self.assertTrue(dashboard_bridge.register(self.project))
        self.assertEqual(len(self.received), 1)
        call = self.received[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], f"/api/v1/aisc_project/{self.project.pid}")
        self.assertEqual(call["token"], TOKEN)
        self.assertEqual(call["body"], {"slug": "assessment-two", "name": "Assessment Two"})

    def test_unregister_deletes_the_same_path(self):
        self.assertTrue(dashboard_bridge.unregister(self.project))
        self.assertEqual(self.received[0]["method"], "DELETE")
        self.assertEqual(self.received[0]["path"], f"/api/v1/aisc_project/{self.project.pid}")
        self.assertEqual(self.received[0]["token"], TOKEN)

    def test_a_tool_tile_carries_the_dashboards_own_body(self):
        # The dashboard's bridge reads `visualizations` (aisc_ext.plugin_tiles.plugin_request),
        # not `charts`, and needs the plugin's version and the project's name to make the tile.
        answer = dashboard_bridge.sync_tool(
            self.project,
            "strongreject::StrongReject",
            "StrongReject",
            version="1.2.0",
            project_name="Assessment Two",
            visualizations=[{"chart_type": "bar", "metrics": ["harmful_rate"]}],
        )
        call = self.received[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["path"], f"/api/v1/aisc_project/{self.project.pid}/plugins")
        self.assertEqual(
            call["body"],
            {
                "plugin": "strongreject::StrongReject",
                "label": "StrongReject",
                "version": "1.2.0",
                "project_name": "Assessment Two",
                "visualizations": [{"chart_type": "bar", "metrics": ["harmful_rate"]}],
                "again": False,
            },
        )
        self.assertEqual(answer, {"plugin": "strongreject::StrongReject", "slug": "assessed",
                                  "charts": 2, "deleted": False})

    def test_a_deleted_tile_is_made_again_only_when_asked(self):
        dashboard_bridge.sync_tool(self.project, "fairness", "Fairness", again=True)
        self.assertIs(self.received[0]["body"]["again"], True)

    def test_a_dashboard_that_answers_500_never_stops_a_person(self):
        _Recorder.status = 500
        self.assertFalse(dashboard_bridge.register(self.project))
        self.assertFalse(dashboard_bridge.unregister(self.project))
        with self.assertRaises(dashboard_bridge.BridgeUnavailable):
            dashboard_bridge.sync_tool(self.project, "fairness", "Fairness")

    @override_settings(RESULTS_DASHBOARD_BRIDGE_URL="http://127.0.0.1:9")
    def test_a_dashboard_that_is_not_there_never_stops_a_person(self):
        self.assertFalse(dashboard_bridge.register(self.project))
        self.assertFalse(dashboard_bridge.unregister(self.project))


@override_settings(RESULTS_DASHBOARD_BRIDGE_URL="http://127.0.0.1:9",
                   RESULTS_DASHBOARD_BRIDGE_TOKEN=TOKEN)
class RouterTest(TestCase):
    """The two write endpoints: an editor may ask, a viewer may not."""

    async def test_an_editor_gets_the_answer_the_dashboard_gave(self):
        from ninja.testing import TestAsyncClient

        from aisc_backend.models.project_member import MembershipRole, ProjectMember
        from aisc_backend.routers.results import router

        project = await Project.objects.acreate(name="Bridge Router", description="",
                                                status="Ready")
        await ProjectMember.objects.acreate(project=project, subject="development",
                                            role=MembershipRole.Editor)
        viewer_project = await Project.objects.acreate(name="Read Only", description="",
                                                      status="Ready")
        await ProjectMember.objects.acreate(project=viewer_project, subject="development",
                                            role=MembershipRole.Viewer)
        client = TestAsyncClient(router)

        # a viewer may read the page but not tell the dashboard anything
        refused = await client.post(f"/{viewer_project.pid}/results/dashboard")
        self.assertEqual(refused.status_code, 403)

        response = await client.post(f"/{project.pid}/results/dashboard")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # nothing is listening on port 9: reported, not raised
        self.assertFalse(body["registered"])
        self.assertTrue(body["bridge_configured"])

        removed = await client.delete(f"/{project.pid}/results/dashboard")
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(removed.json()["removed"])
