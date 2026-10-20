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


class _RpcError(Exception):
    """A gRPC error as the immudb SDK raises it: a status code and the server's message."""

    def __init__(self, code_name: str, details: str):
        super().__init__(details)
        self._code_name, self._details = code_name, details

    def code(self):
        import grpc

        return getattr(grpc.StatusCode, self._code_name)

    def details(self):
        return self._details


class AnEndedSessionIsOpenedAgain(SimpleTestCase):
    """immudb ends a login when its token expires (IMMUDB_TOKEN_EXPIRY_TIME) and at every restart. The clerk
    logs in again and retries the call once, instead of losing every audit write until the engine restarts."""

    def _clerk(self, clients):
        patcher = mock.patch.object(clerk_mod, "ImmudbClient", side_effect=clients)
        patcher.start()
        self.addCleanup(patcher.stop)
        for client in clients:
            client.databaseList.return_value = [b"auditdb"]
        return clerk_mod.AuditClerk()

    def test_a_write_after_the_session_ended_logs_in_again_and_is_written(self):
        for ended in (_RpcError("UNAUTHENTICATED", "invalid token"), _RpcError("UNKNOWN", "token has expired"),
                      _RpcError("UNKNOWN", "session not found"), _RpcError("UNKNOWN", "please login")):
            with self.subTest(ended=ended.details()):
                first, second = mock.MagicMock(), mock.MagicMock()
                c = self._clerk([first, second])
                c.connect()
                first.sqlExec.side_effect = ended
                c.write_event(actor="admin", action="delete", resource_type="project")
                second.login.assert_called_once()
                self.assertIn("INSERT", second.sqlExec.call_args_list[-1].args[0])

    def test_any_other_failure_is_not_retried(self):
        first, second = mock.MagicMock(), mock.MagicMock()
        c = self._clerk([first, second])
        c.connect()
        first.sqlExec.side_effect = _RpcError("INVALID_ARGUMENT", "syntax error")
        with self.assertRaises(_RpcError):
            c.write_event(actor="admin", action="delete", resource_type="project")
        second.login.assert_not_called()

    def test_a_session_that_ends_again_right_away_is_not_retried_for_ever(self):
        first, second = mock.MagicMock(), mock.MagicMock()
        c = self._clerk([first, second])
        c.connect()
        first.sqlExec.side_effect = _RpcError("UNAUTHENTICATED", "invalid token")
        second.sqlExec.side_effect = [None, _RpcError("UNAUTHENTICATED", "invalid token")]   # CREATE TABLE, then the insert
        with self.assertRaises(_RpcError):
            c.write_event(actor="admin", action="delete", resource_type="project")

    def test_the_integrity_check_survives_an_ended_session_too(self):
        first, second = mock.MagicMock(), mock.MagicMock()
        c = self._clerk([first, second])
        c.connect()
        first.verifiedSet.side_effect = _RpcError("UNAUTHENTICATED", "invalid token")
        second.verifiedGet.return_value = mock.Mock(value=b"ok")
        self.assertTrue(c.verify())
        second.login.assert_called_once()


class AnEndedSessionFoundWhileReading(SimpleTestCase):
    """After an immudb restart the server answers "not logged in", and a query's rows come back as a stream:
    the refusal surfaces only when they are read, which must still be inside the retry."""

    def test_reading_the_log_after_a_restart_logs_in_again(self):
        first, second = mock.MagicMock(), mock.MagicMock()
        for client in (first, second):
            client.databaseList.return_value = [b"auditdb"]

        def refused():
            raise _RpcError("UNKNOWN", "not logged in")
            yield  # a generator: the refusal comes when the rows are read

        first.sqlQuery.side_effect = lambda *a, **k: refused()
        second.sqlQuery.return_value = iter([])
        with mock.patch.object(clerk_mod, "ImmudbClient", side_effect=[first, second]):
            c = clerk_mod.AuditClerk()
            c.connect()
            self.assertEqual(c.list_events(), [])
        second.login.assert_called_once()
