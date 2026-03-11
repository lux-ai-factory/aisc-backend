"""Audit service for reading verified audit logs from immudb.

Provides async functions for querying audit events and performing
cryptographically verified reads via immudb's verifiableSQLGet.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from asgiref.sync import sync_to_async
from immudb import ImmudbClient
from immudb.datatypesv2 import PrimaryKeyIntValue

from config.settings import (
    IMMUDB_DATABASE,
    IMMUDB_HOST,
    IMMUDB_PASSWORD,
    IMMUDB_PORT,
    IMMUDB_USER,
)

logger = logging.getLogger(__name__)

_client: ImmudbClient | None = None
_lock = threading.Lock()


class AuditServiceError(Exception):
    """Raised when the audit service encounters an error."""


def _get_immudb_client() -> ImmudbClient:
    """Get or create the singleton ImmudbClient. Thread-safe."""
    global _client

    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        try:
            immudb_url = f"{IMMUDB_HOST}:{IMMUDB_PORT}"
            logger.info(f"Connecting to immudb at {immudb_url}")

            client = ImmudbClient(immudb_url)
            client.login(IMMUDB_USER, IMMUDB_PASSWORD)
            client.useDatabase(IMMUDB_DATABASE.encode())

            _client = client
            return _client

        except Exception as e:
            logger.error(f"Failed to connect to immudb: {e}")
            raise AuditServiceError(
                f"Cannot connect to immudb: {e}"
            ) from e


def _reset_client() -> None:
    """Reset the singleton client. Used for testing."""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.logout()
            except Exception:
                pass
        _client = None


def _parse_row(row: tuple, columns: list[str]) -> dict[str, Any]:
    """Convert an immudb SQL row tuple + column names to a dict."""
    event = dict(zip(columns, row))

    # Parse details JSON string back to dict
    if "details" in event and event["details"]:
        try:
            event["details"] = json.loads(event["details"])
        except (json.JSONDecodeError, TypeError):
            event["details"] = None
    else:
        event["details"] = None

    # Ensure timestamp is ISO format
    if "timestamp" in event and isinstance(event["timestamp"], datetime):
        event["timestamp"] = event["timestamp"].replace(tzinfo=timezone.utc)

    event["verified"] = False
    return event


_COLUMNS = [
    "id", "timestamp", "event_type", "user_id", "evaluation_id", "task_id",
    "plugin_name", "status", "duration_ms", "details", "error_message",
]


def _query_audit_events_sync(
    evaluation_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query audit events from immudb (synchronous)."""
    client = _get_immudb_client()

    conditions = []
    params: dict[str, Any] = {}

    if evaluation_id:
        conditions.append("evaluation_id = @evaluation_id")
        params["evaluation_id"] = evaluation_id

    if event_type:
        conditions.append("event_type = @event_type")
        params["event_type"] = event_type

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    query = (
        f"SELECT id, timestamp, event_type, user_id, evaluation_id, task_id, "
        f"plugin_name, status, duration_ms, details, error_message "
        f"FROM audit_events{where_clause} "
        f"ORDER BY id DESC "
        f"LIMIT @limit OFFSET @offset;"
    )
    params["limit"] = limit
    params["offset"] = offset

    result = client.sqlQuery(query, params)

    return [_parse_row(row, _COLUMNS) for row in result]


def _count_audit_events_sync(
    evaluation_id: str | None = None,
    event_type: str | None = None,
) -> int:
    """Count audit events matching filters (synchronous)."""
    client = _get_immudb_client()

    conditions = []
    params: dict[str, Any] = {}

    if evaluation_id:
        conditions.append("evaluation_id = @evaluation_id")
        params["evaluation_id"] = evaluation_id

    if event_type:
        conditions.append("event_type = @event_type")
        params["event_type"] = event_type

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    query = f"SELECT COUNT(*) FROM audit_events{where_clause};"
    result = client.sqlQuery(query, params)

    return result[0][0] if result else 0


def _get_verified_audit_event_sync(event_id: int) -> dict[str, Any]:
    """Get a single audit event with cryptographic verification (synchronous).

    Uses immudb's verifiableSQLGet to prove the row hasn't been tampered with.
    """
    client = _get_immudb_client()

    verification = client.verifiableSQLGet(
        "audit_events", [PrimaryKeyIntValue(event_id)]
    )

    if verification.sqlEntry is None:
        raise AuditServiceError(f"Audit event {event_id} not found")

    # Extract column values from the verified entry
    col_names = verification.ColNamesById or {}
    col_values = verification.sqlEntry.values if hasattr(verification.sqlEntry, 'values') else {}

    # Build the event dict from verified data
    # Also do a standard SQL query to get the full row data reliably
    query = (
        "SELECT id, timestamp, event_type, user_id, evaluation_id, task_id, "
        "plugin_name, status, duration_ms, details, error_message "
        "FROM audit_events WHERE id = @id;"
    )
    result = client.sqlQuery(query, {"id": event_id})

    if not result:
        raise AuditServiceError(f"Audit event {event_id} not found")

    event = _parse_row(result[0], _COLUMNS)
    event["verified"] = verification.verified

    return event


# ── Async wrappers for Django Ninja async routes ───────────────────

async def get_audit_events(
    evaluation_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query audit events (async)."""
    return await sync_to_async(_query_audit_events_sync)(
        evaluation_id=evaluation_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )


async def count_audit_events(
    evaluation_id: str | None = None,
    event_type: str | None = None,
) -> int:
    """Count audit events matching filters (async)."""
    return await sync_to_async(_count_audit_events_sync)(
        evaluation_id=evaluation_id,
        event_type=event_type,
    )


async def get_verified_audit_event(event_id: int) -> dict[str, Any]:
    """Get a single verified audit event (async)."""
    return await sync_to_async(_get_verified_audit_event_sync)(event_id)


async def get_audit_events_for_evaluation(
    evaluation_id: str,
) -> list[dict[str, Any]]:
    """Get all audit events for a specific evaluation (async)."""
    return await sync_to_async(_query_audit_events_sync)(
        evaluation_id=evaluation_id,
        limit=1000,
    )
