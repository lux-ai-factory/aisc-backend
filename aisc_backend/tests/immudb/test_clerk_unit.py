"""
UNIT tests for aisc_backend.audit.clerk.AuditClerk — NO live immudb, NO database (SimpleTestCase).

Strategy: patch `ImmudbClient` with a Mock so we assert the CLERK's logic without a server:
  - idempotent provisioning (createDatabase only when the db is missing from databaseList),
  - useDatabase + CREATE TABLE IF NOT EXISTS run,
  - write_event issues a parameterized INSERT with the standard audit fields (metadata JSON-encoded).

Run:  uv run manage.py test aisc_backend.tests.immudb.test_clerk_unit
"""
import json
import unittest.mock as mock
from datetime import datetime

from django.test import SimpleTestCase

from aisc_backend.audit import clerk as clerk_mod


class AuditClerkUnitTest(SimpleTestCase):
    def _make_clerk_with_mock(self, existing_dbs):
        """Return (clerk, mock_client) with ImmudbClient patched; databaseList() -> existing_dbs."""
        mock_client = mock.MagicMock()
        mock_client.databaseList.return_value = existing_dbs
        patcher = mock.patch.object(clerk_mod, "ImmudbClient", return_value=mock_client)
        patcher.start()
        self.addCleanup(patcher.stop)
        c = clerk_mod.AuditClerk()   # fresh instance, not the shared singleton
        return c, mock_client

    def test_connect_creates_db_when_missing(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[])   # auditdb NOT present
        c.connect()
        m.login.assert_called_once()
        m.createDatabase.assert_called_once()
        (arg,), _ = m.createDatabase.call_args
        self.assertIsInstance(arg, bytes)                    # db name passed as BYTES
        m.useDatabase.assert_called_once()
        create_sql = m.sqlExec.call_args_list[0].args[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS audit_log", create_sql)

    def test_connect_skips_create_when_db_exists(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.connect()
        m.createDatabase.assert_not_called()                 # idempotent
        m.useDatabase.assert_called_once()

    def test_connect_is_called_once_even_if_invoked_twice(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.connect()
        c.connect()                       # second call must be a no-op (already connected)
        m.login.assert_called_once()

    def test_write_event_issues_parameterized_insert(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.write_event(actor="admin", action="delete", resource_type="project",
                      resource_id="abc-123", source_app="backend", source_ip="10.0.0.5",
                      outcome="ok", metadata={"reason": "cleanup"})
        insert_call = m.sqlExec.call_args_list[-1]           # last sqlExec = the INSERT
        sql = insert_call.args[0]
        params = insert_call.kwargs["params"]
        self.assertIn("INSERT INTO audit_log", sql)
        self.assertIn("@actor", sql)                          # parameterized, not string-formatted
        self.assertEqual(params["actor"], "admin")
        self.assertEqual(params["action"], "delete")
        self.assertEqual(params["resource_type"], "project")
        self.assertEqual(params["resource_id"], "abc-123")
        self.assertEqual(params["source_app"], "backend")
        self.assertEqual(params["source_ip"], "10.0.0.5")
        self.assertEqual(params["outcome"], "ok")
        self.assertEqual(json.loads(params["metadata"]), {"reason": "cleanup"})   # JSON-encoded
        self.assertIsInstance(params["occurred_at"], datetime)                    # server-stamped time

    def test_write_event_defaults(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.write_event(actor="user", action="login", resource_type="session")
        params = m.sqlExec.call_args_list[-1].kwargs["params"]
        self.assertEqual(json.loads(params["metadata"]), {})   # metadata defaults to empty
        self.assertEqual(params["outcome"], "ok")              # outcome defaults to ok
        self.assertEqual(params["source_app"], "backend")      # source_app defaults to backend
        self.assertIsNone(params["resource_id"])               # optional

    def test_write_event_includes_readable_summary(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.write_event(actor="admin", action="create", resource_type="project",
                      resource_id="p-9", metadata={"name": "Demo"})
        summary = m.sqlExec.call_args_list[-1].kwargs["params"]["summary"]
        self.assertIn("admin", summary)
        self.assertIn("create", summary)
        self.assertIn("project", summary)
        self.assertIn("p-9", summary)
        self.assertIn("name=Demo", summary)
