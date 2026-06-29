"""
UNIT tests for aisc_backend.audit.clerk.AuditClerk — NO live immudb, NO database (SimpleTestCase).

Strategy: patch `ImmudbClient` with a Mock so we assert the CLERK's logic without a server:
  - idempotent provisioning (createDatabase only when the db is missing from databaseList),
  - useDatabase + CREATE TABLE IF NOT EXISTS run,
  - write_event issues a parameterized INSERT with the right @params (consequence JSON-encoded).

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
        # createDatabase called with the db name as BYTES
        m.createDatabase.assert_called_once()
        (arg,), _ = m.createDatabase.call_args
        self.assertIsInstance(arg, bytes)
        m.useDatabase.assert_called_once()
        # CREATE TABLE IF NOT EXISTS executed
        create_sql = m.sqlExec.call_args_list[0].args[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS audit_log", create_sql)

    def test_connect_skips_create_when_db_exists(self):
        # auditdb already present (as bytes, like the SDK returns) -> must NOT create again (idempotent)
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.connect()
        m.createDatabase.assert_not_called()
        m.useDatabase.assert_called_once()

    def test_connect_is_called_once_even_if_invoked_twice(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.connect()
        c.connect()                       # second call must be a no-op (already connected)
        m.login.assert_called_once()      # only logged in once

    def test_write_event_issues_parameterized_insert(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.write_event("admin", "project:delete", "backend", {"projectPid": "abc-123"}, status="ok")
        # last sqlExec call is the INSERT (first was CREATE TABLE during connect)
        insert_call = m.sqlExec.call_args_list[-1]
        sql = insert_call.args[0]
        params = insert_call.kwargs["params"]
        self.assertIn("INSERT INTO audit_log", sql)
        self.assertIn("@who", sql)                       # parameterized, not string-formatted
        self.assertEqual(params["who"], "admin")
        self.assertEqual(params["what"], "project:delete")
        self.assertEqual(params["app"], "backend")
        self.assertEqual(params["status"], "ok")
        self.assertEqual(json.loads(params["consequence"]), {"projectPid": "abc-123"})  # JSON-encoded
        self.assertIsInstance(params["occurred_at"], datetime)                          # server-stamped time

    def test_write_event_defaults_consequence_to_empty_json(self):
        c, m = self._make_clerk_with_mock(existing_dbs=[b"auditdb"])
        c.write_event("user", "auth:login", "controls")
        params = m.sqlExec.call_args_list[-1].kwargs["params"]
        self.assertEqual(json.loads(params["consequence"]), {})
        self.assertEqual(params["status"], "ok")   # default status
