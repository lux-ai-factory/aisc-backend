"""Writing an event to the audit log is work in a project: the caller names the project, is a member of it,
and is at least an editor. Reading the log stays an admin's, project or not.

Configurator only: standalone has no memberships."""
import unittest.mock as mock

from aisc_backend.audit import clerk as clerk_module
from aisc_backend.tests.isolation_support import configurator_only
from aisc_backend.tests.routers.test_project_membership import OPENS, _AtTheDoor

BODY = {"action": "update", "resource_type": "checklist"}
#: a body today's route also takes, so a refusal below can only come from the door
WITH_SOURCE = {**BODY, "source_app": "controls"}


@configurator_only
class WritingToTheAuditLogTakesAnEditorTestCase(_AtTheDoor):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(clerk_module.clerk, "write_event")
        self.written = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stranger_is_told_there_is_no_such_project(self):
        self.assertEqual(self.answer("POST", "/api/v1/audit", None, WITH_SOURCE), 404)
        self.written.assert_not_called()

    def test_a_viewer_may_not_write(self):
        self.assertEqual(self.answer("POST", "/api/v1/audit", "viewer", WITH_SOURCE), 403)
        self.written.assert_not_called()

    def test_an_editor_may(self):
        self.assertEqual(self.answer("POST", "/api/v1/audit", "editor", BODY), OPENS)

    def test_without_a_project_it_is_refused(self):
        response = self.call("POST", "/api/v1/audit", {"Authorization": self.bearer()}, WITH_SOURCE)
        self.assertEqual(response.status_code, 400, getattr(response, "content", b"")[:300])
        self.written.assert_not_called()

    def test_reading_the_log_needs_no_project(self):
        with mock.patch.object(clerk_module.clerk, "verify", return_value=True), \
             mock.patch.object(clerk_module.clerk, "list_events", return_value=[]):
            response = self.call("GET", "/api/v1/audit", {"Authorization": self.bearer(("admin",))})
        self.assertEqual(response.status_code, 200, getattr(response, "content", b"")[:300])


@configurator_only
class ARunIsRenamedAndStoppedByAnEditorTestCase(_AtTheDoor):
    EVALUATION = "6f1c2a3b-0000-4000-8000-00000000e7a1"

    def test_a_viewer_may_not_rename_a_run(self):
        self.assertEqual(self.answer("PATCH", f"/api/v1/evaluations/{self.EVALUATION}/name", "viewer",
                                     {"name": "x"}), 403)

    def test_a_viewer_may_not_stop_a_run(self):
        self.assertEqual(self.answer("POST", f"/api/v1/evaluations/{self.EVALUATION}/stop", "viewer"), 403)
