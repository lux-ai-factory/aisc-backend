"""
The AuditClerk — the ONE writer to the immudb tamper-proof audit ledger.

Design (see personal-docs/immudb-integration/): every app's "who did what, where, with what consequence"
is recorded immutably in immudb. The backend is the single writer; other apps reach it via the /audit
endpoint, and the backend's own code calls write_event() directly. This module is that single writer.

Three jobs:
  1. connect()      — open an immudb client + log in (once).
  2. provision      — ensure the audit database exists (idempotent) + CREATE TABLE IF NOT EXISTS.
  3. write_event()  — INSERT one audit row (parameterized).

immudb-py API facts verified against the installed SDK (1.5.0):
  - ImmudbClient("host:port"); login(user, pwd); databaseList(); createDatabase(b"name");
    useDatabase(b"name"); sqlExec(stmt, params={}); SQL params use @name.
  - DB names are BYTES. createDatabase is NOT idempotent (raises if exists) -> we check databaseList first.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from django.conf import settings
from immudb import ImmudbClient

logger = logging.getLogger(__name__)

# The audit table. CREATE TABLE IF NOT EXISTS is idempotent (safe every startup).
# Columns avoid SQL reserved words: `app` (not "where"), `occurred_at` (not "when").
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER AUTO_INCREMENT,
    who          VARCHAR[256],
    what         VARCHAR[128],
    app          VARCHAR[32],
    occurred_at  TIMESTAMP,
    consequence  VARCHAR,
    status       VARCHAR[16],
    summary      VARCHAR,
    PRIMARY KEY (id)
);
"""

# For tables created before `summary` existed: add the column (idempotent — guarded in connect()).
_ADD_SUMMARY = "ALTER TABLE audit_log ADD COLUMN summary VARCHAR;"

_INSERT = (
    "INSERT INTO audit_log (who, what, app, occurred_at, consequence, status, summary) "
    "VALUES (@who, @what, @app, @occurred_at, @consequence, @status, @summary);"
)


def _build_summary(who: str, what: str, app: str, consequence: dict, status: str) -> str:
    """A plain-English one-liner so the raw ledger row is readable at a glance (no JSON decoding)."""
    detail = ", ".join(f"{k}={v}" for k, v in (consequence or {}).items())
    line = f"{who} performed '{what}' in {app}"
    if detail:
        line += f" ({detail})"
    if status != "ok":
        line += f" [{status}]"
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
            # createDatabase has no "IF NOT EXISTS" and raises if it exists -> create only if missing.
            existing = [d.decode() if isinstance(d, bytes) else d for d in client.databaseList()]
            if db not in existing:
                client.createDatabase(db_bytes)
                logger.info("immudb: created audit database %r", db)
            client.useDatabase(db_bytes)

            client.sqlExec(_CREATE_TABLE)  # idempotent (IF NOT EXISTS)
            # Add `summary` to tables created before it existed. immudb has no "ADD COLUMN IF NOT EXISTS",
            # so attempt it and ignore the error if the column is already there (idempotent).
            try:
                client.sqlExec(_ADD_SUMMARY)
                logger.info("immudb: added summary column to audit_log")
            except Exception:  # noqa: BLE001 - column already exists on an up-to-date table
                pass
            self._client = client
            logger.info("immudb audit clerk ready (db=%r)", db)

    def write_event(
        self,
        who: str,
        what: str,
        app: str,
        consequence: dict | None = None,
        status: str = "ok",
    ) -> None:
        """
        Append one immutable audit event.
          who         - the acting user (from the verified Keycloak token; never user-supplied free text)
          what        - "object:verb", e.g. "project:delete"
          app         - which app: backend | webapp | controls | qualification
          consequence - dict of what actually changed (stored as JSON)
          status      - "ok" | "failed"
        """
        self.connect()
        assert self._client is not None
        consequence = consequence or {}
        self._client.sqlExec(
            _INSERT,
            params={
                "who": who,
                "what": what,
                "app": app,
                "occurred_at": datetime.now(timezone.utc),
                "consequence": json.dumps(consequence),
                "status": status,
                # a readable one-liner stored IN the immutable row (auditor reads it at a glance)
                "summary": _build_summary(who, what, app, consequence, status),
            },
        )


# Module-level singleton: the rest of the app imports this ONE instance.
clerk = AuditClerk()
