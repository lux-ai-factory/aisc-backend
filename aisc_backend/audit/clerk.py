"""
The AuditClerk — the ONE writer to the immudb tamper-proof audit ledger.

Every app's action ("who did what to which resource, when, from where, with what outcome") is recorded
immutably in immudb. The backend is the single writer; other apps reach it via the /audit endpoint, and
the backend's own code calls write_event() directly.

Schema follows standard audit-log practice (queryable fields, not one opaque string):
  occurred_at, actor, action, resource_type, resource_id, source_app, source_ip, outcome, metadata, summary
so an auditor can query "all deletes" or "everything done to project X" — plus source_ip for "where from".

immudb-py API facts (SDK 1.5.0): ImmudbClient("host:port"); login(user,pwd); databaseList();
createDatabase(b"name") (bytes, NOT idempotent -> guard on databaseList); useDatabase(b"name");
sqlExec(stmt, params={}) with @name params.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from django.conf import settings
from immudb import ImmudbClient

logger = logging.getLogger(__name__)

# The audit table (standard audit-log columns). CREATE TABLE IF NOT EXISTS is idempotent.
# VARCHAR needs a max length in immudb; metadata + summary are unbounded VARCHAR.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER AUTO_INCREMENT,
    occurred_at    TIMESTAMP,
    actor          VARCHAR[256],
    action         VARCHAR[64],
    resource_type  VARCHAR[64],
    resource_id    VARCHAR[256],
    source_app     VARCHAR[32],
    source_ip      VARCHAR[64],
    outcome        VARCHAR[16],
    metadata       VARCHAR,
    summary        VARCHAR,
    PRIMARY KEY (id)
);
"""

_COLUMNS = [
    "id", "occurred_at", "actor", "action", "resource_type", "resource_id",
    "source_app", "source_ip", "outcome", "metadata", "summary",
]

_INSERT = (
    "INSERT INTO audit_log "
    "(occurred_at, actor, action, resource_type, resource_id, source_app, source_ip, outcome, metadata, summary) "
    "VALUES (@occurred_at, @actor, @action, @resource_type, @resource_id, @source_app, @source_ip, @outcome, @metadata, @summary);"
)


def _build_summary(actor: str, action: str, resource_type: str, resource_id: str | None,
                   source_app: str, source_ip: str | None, outcome: str, metadata: dict) -> str:
    """A plain-English one-liner so the raw ledger row is readable at a glance (no JSON decoding)."""
    target = resource_type + (f" {resource_id}" if resource_id else "")
    line = f"{actor} {action} {target} via {source_app}"
    if source_ip:
        line += f" from {source_ip}"
    detail = ", ".join(f"{k}={v}" for k, v in (metadata or {}).items())
    if detail:
        line += f" ({detail})"
    if outcome != "ok":
        line += f" [{outcome}]"
    return line


class AuditClerk:
    """Single writer to the immudb audit ledger. Connects + provisions lazily on first use."""

    def __init__(self) -> None:
        self._client: ImmudbClient | None = None
        self._lock = threading.Lock()  # guard connect() against concurrent first-writes

    def connect(self) -> None:
        """Open the client, log in, ensure the audit DB + table exist. Idempotent + thread-safe."""
        if self._client is not None:
            return
        with self._lock:
            if self._client is not None:  # re-check inside the lock
                return
            client = ImmudbClient(settings.IMMUDB_URL)
            client.login(settings.IMMUDB_USER, settings.IMMUDB_PASSWORD)

            db = settings.IMMUDB_DATABASE
            db_bytes = db.encode()
            existing = [d.decode() if isinstance(d, bytes) else d for d in client.databaseList()]
            if db not in existing:
                client.createDatabase(db_bytes)
                logger.info("immudb: created audit database %r", db)
            client.useDatabase(db_bytes)

            client.sqlExec(_CREATE_TABLE)  # idempotent (IF NOT EXISTS)
            self._client = client
            logger.info("immudb audit clerk ready (db=%r)", db)

    def write_event(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        source_app: str = "backend",
        source_ip: str | None = None,
        outcome: str = "ok",
        metadata: dict | None = None,
    ) -> None:
        """
        Append one immutable audit event (standard audit fields).
          actor         - WHO: the acting user (from the verified token; never user-supplied free text)
          action        - WHAT: the verb, e.g. create | update | delete | run | upload
          resource_type - the object type acted on, e.g. project | dataset | evaluation | checklist
          resource_id   - WHICH object (id/pid), optional
          source_app    - WHERE: backend | webapp | controls | qualification
          source_ip     - WHERE FROM: the client IP (best-effort)
          outcome       - ok | failed
          metadata      - WHY/details: dict of what actually changed (stored as JSON)
        """
        self.connect()
        assert self._client is not None
        metadata = metadata or {}
        self._client.sqlExec(
            _INSERT,
            params={
                "occurred_at": datetime.now(timezone.utc),
                "actor": actor,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "source_app": source_app,
                "source_ip": source_ip,
                "outcome": outcome,
                "metadata": json.dumps(metadata),
                "summary": _build_summary(actor, action, resource_type, resource_id,
                                          source_app, source_ip, outcome, metadata),
            },
        )

    def list_events(self, limit: int = 100) -> list[dict]:
        """Read recent audit events (newest first). ADMIN-only use (gated at the endpoint)."""
        self.connect()
        assert self._client is not None
        rows = self._client.sqlQuery(
            "SELECT id, occurred_at, actor, action, resource_type, resource_id, "
            "source_app, source_ip, outcome, metadata, summary FROM audit_log;"
        )
        events = []
        for r in rows:
            e = dict(zip(_COLUMNS, r))
            ts = e.get("occurred_at")
            e["occurred_at"] = ts.isoformat() if hasattr(ts, "isoformat") else ts  # JSON-friendly
            events.append(e)
        events.sort(key=lambda e: e["id"], reverse=True)   # newest first
        return events[:limit]

    def verify(self) -> bool:
        """
        Tamper-check the ledger: verifiedSet a sentinel then verifiedGet it. The SDK validates the
        server's cryptographic proof against the saved state — if the server tampered, this raises/fails.
        Returns True iff the cryptographic verification passes.
        """
        self.connect()
        assert self._client is not None
        try:
            self._client.verifiedSet(b"audit:_integrity_probe", b"ok")
            res = self._client.verifiedGet(b"audit:_integrity_probe")
            return getattr(res, "value", None) == b"ok"
        except Exception as e:  # noqa: BLE001
            logger.warning("immudb verify() failed: %s", e)
            return False


# Module-level singleton: the rest of the app imports this ONE instance.
clerk = AuditClerk()
