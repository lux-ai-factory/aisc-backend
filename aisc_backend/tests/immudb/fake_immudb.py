"""In-memory fake of the immudb python SDK's ImmudbClient.

Drop-in replacement used by the integration tests so the audit clerk's
write/query/verified flows run hermetically without a live immudb server
(and therefore without silently skipping in CI).
"""
import re
from types import SimpleNamespace


class FakeImmudbClient:
    """Implements the subset of immudb.ImmudbClient that the AuditClerk uses."""

    def __init__(self, *args, **kwargs):
        self._rows: list[dict] = []
        self._kv: dict[bytes, bytes] = {}
        self._next_id = 0

    # --- connection lifecycle (no-ops) ---
    def login(self, user=None, password=None):
        pass

    def logout(self):
        pass

    def databaseList(self):
        return [b"auditdb"]

    def createDatabase(self, name):
        pass

    def useDatabase(self, name):
        pass

    # --- SQL surface (only the shapes the clerk issues) ---
    def sqlExec(self, stmt, params=None):
        # DDL (CREATE TABLE / ALTER TABLE) provisions the shape, not a row.
        if stmt.lstrip().upper().startswith(("CREATE", "ALTER")):
            return
        self._next_id += 1
        row = {"id": self._next_id}
        row.update({k: v for k, v in (params or {}).items()})
        self._rows.append(row)

    def sqlQuery(self, stmt, params=None):
        select_match = re.search(r"SELECT\s+(.+?)\s+FROM", stmt, re.IGNORECASE | re.DOTALL)
        if not select_match:
            return []
        columns = [c.strip() for c in select_match.group(1).split(",")]

        rows = list(self._rows)
        if "WHERE" in stmt and params:
            # WHERE col = @param (single clause): bind the param value onto its column.
            where_match = re.search(r"WHERE\s+(\w+)\s*=\s*@(\w+)", stmt, re.IGNORECASE)
            if where_match:
                col, param_name = where_match.group(1), where_match.group(2)
                rows = [r for r in rows if r.get(col) == params.get(param_name)]

        return [tuple(r.get(c) for c in columns) for r in rows]

    # --- verified/tamper-check surface ---
    def verifiedSet(self, key, value):
        self._kv[key] = value

    def verifiedGet(self, key):
        return SimpleNamespace(value=self._kv[key])
