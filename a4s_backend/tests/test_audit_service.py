"""Tests for the immudb audit service."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase

from a4s_backend.services.audit_service import (
    AuditServiceError,
    _get_immudb_client,
    _parse_row,
    _query_audit_events_sync,
    _get_verified_audit_event_sync,
    _count_audit_events_sync,
    _reset_client,
    _COLUMNS,
)


def _make_row(
    id=1,
    event_type="EVALUATION_STARTED",
    evaluation_id="abc-123",
    task_id="task-456",
    plugin_name="",
    status="success",
    duration_ms=100,
    details='{"key": "value"}',
    error_message="",
):
    return (
        id,
        datetime(2026, 3, 4, 12, 0, 0),
        event_type,
        evaluation_id,
        task_id,
        plugin_name,
        status,
        duration_ms,
        details,
        error_message,
    )


class TestImmudbClient(TestCase):
    def setUp(self):
        _reset_client()

    def tearDown(self):
        _reset_client()

    @patch("a4s_backend.services.audit_service.ImmudbClient")
    def test_get_client_connects(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        client = _get_immudb_client()

        self.assertIs(client, mock_client)
        mock_client.login.assert_called_once()
        mock_client.useDatabase.assert_called_once()

    @patch("a4s_backend.services.audit_service.ImmudbClient")
    def test_get_client_returns_singleton(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        c1 = _get_immudb_client()
        c2 = _get_immudb_client()

        self.assertIs(c1, c2)
        self.assertEqual(mock_cls.call_count, 1)

    @patch("a4s_backend.services.audit_service.ImmudbClient")
    def test_get_client_raises_on_failure(self, mock_cls):
        mock_cls.side_effect = ConnectionError("refused")

        with self.assertRaises(AuditServiceError):
            _get_immudb_client()


class TestParseRow(TestCase):
    def test_parses_basic_row(self):
        row = _make_row()
        result = _parse_row(row, _COLUMNS)

        self.assertEqual(result["id"], 1)
        self.assertEqual(result["event_type"], "EVALUATION_STARTED")
        self.assertEqual(result["evaluation_id"], "abc-123")
        self.assertEqual(result["details"], {"key": "value"})
        self.assertFalse(result["verified"])

    def test_parses_empty_details(self):
        row = _make_row(details="")
        result = _parse_row(row, _COLUMNS)
        self.assertIsNone(result["details"])

    def test_parses_invalid_json_details(self):
        row = _make_row(details="not json")
        result = _parse_row(row, _COLUMNS)
        self.assertIsNone(result["details"])

    def test_adds_utc_timezone(self):
        row = _make_row()
        result = _parse_row(row, _COLUMNS)
        self.assertEqual(result["timestamp"].tzinfo, timezone.utc)


class TestQueryAuditEvents(TestCase):
    def setUp(self):
        _reset_client()

    def tearDown(self):
        _reset_client()

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_query_all_events(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.sqlQuery.return_value = [_make_row(), _make_row(id=2)]
        mock_get_client.return_value = mock_client

        result = _query_audit_events_sync()

        self.assertEqual(len(result), 2)
        mock_client.sqlQuery.assert_called_once()

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_query_with_evaluation_filter(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.sqlQuery.return_value = [_make_row()]
        mock_get_client.return_value = mock_client

        _query_audit_events_sync(evaluation_id="abc-123")

        query = mock_client.sqlQuery.call_args[0][0]
        params = mock_client.sqlQuery.call_args[0][1]
        self.assertIn("evaluation_id = @evaluation_id", query)
        self.assertEqual(params["evaluation_id"], "abc-123")

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_query_with_event_type_filter(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.sqlQuery.return_value = []
        mock_get_client.return_value = mock_client

        _query_audit_events_sync(event_type="PLUGIN_COMPLETED")

        params = mock_client.sqlQuery.call_args[0][1]
        self.assertEqual(params["event_type"], "PLUGIN_COMPLETED")


class TestCountAuditEvents(TestCase):
    def setUp(self):
        _reset_client()

    def tearDown(self):
        _reset_client()

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_count_returns_integer(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.sqlQuery.return_value = [(42,)]
        mock_get_client.return_value = mock_client

        count = _count_audit_events_sync()
        self.assertEqual(count, 42)

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_count_empty_returns_zero(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.sqlQuery.return_value = []
        mock_get_client.return_value = mock_client

        count = _count_audit_events_sync()
        self.assertEqual(count, 0)


class TestVerifiedAuditEvent(TestCase):
    def setUp(self):
        _reset_client()

    def tearDown(self):
        _reset_client()

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_verified_read_returns_verified_true(self, mock_get_client):
        mock_client = MagicMock()

        verification = MagicMock()
        verification.verified = True
        verification.sqlEntry = MagicMock()
        verification.ColNamesById = {}
        mock_client.verifiableSQLGet.return_value = verification
        mock_client.sqlQuery.return_value = [_make_row(id=42)]
        mock_get_client.return_value = mock_client

        result = _get_verified_audit_event_sync(42)

        self.assertTrue(result["verified"])
        self.assertEqual(result["id"], 42)
        mock_client.verifiableSQLGet.assert_called_once()

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_verified_read_returns_verified_false(self, mock_get_client):
        mock_client = MagicMock()

        verification = MagicMock()
        verification.verified = False
        verification.sqlEntry = MagicMock()
        verification.ColNamesById = {}
        mock_client.verifiableSQLGet.return_value = verification
        mock_client.sqlQuery.return_value = [_make_row(id=1)]
        mock_get_client.return_value = mock_client

        result = _get_verified_audit_event_sync(1)

        self.assertFalse(result["verified"])

    @patch("a4s_backend.services.audit_service._get_immudb_client")
    def test_verified_read_not_found_raises(self, mock_get_client):
        mock_client = MagicMock()

        verification = MagicMock()
        verification.sqlEntry = None
        mock_client.verifiableSQLGet.return_value = verification
        mock_get_client.return_value = mock_client

        with self.assertRaises(AuditServiceError):
            _get_verified_audit_event_sync(999)
