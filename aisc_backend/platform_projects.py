"""What the engine reads of the platform's projects.

The platform owns `core.project` in the platform database; the engine has
SELECT on it and nothing more, through the `platform` alias (isolation I7.1:
the engine's own rows live in the project's database). The engine's own project
row is not a second project someone creates; it is this service's side of the
platform project it was opened on, so the only things it needs from the
platform are the name to show and whether the project exists.

Absent `core` (the sqlite database the test runner builds, or an engine running
on its own) there is no name to read, and the caller names the row itself.
"""
from __future__ import annotations

from django.db import DEFAULT_DB_ALIAS, connections


def _platform():
    """The platform database: its own alias on project databases, else the one database."""
    return connections["platform"] if "platform" in connections.settings else connections[DEFAULT_DB_ALIAS]


def platform_project_name(platform_project_id) -> str | None:
    """The platform's name for this project, or None when it cannot be read."""
    platform = _platform()
    if platform.vendor != "postgresql":
        return None
    with platform.cursor() as cursor:
        cursor.execute("SELECT to_regclass('core.project') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(
            "SELECT name FROM core.project WHERE pid = %s", [str(platform_project_id)]
        )
        row = cursor.fetchone()
        return row[0] if row else None


def platform_project_exists(platform_project_id) -> bool:
    """Whether the platform has this project (I7.2: the door's last check).

    True when there is no platform table to ask (sqlite, one database without
    `core`): there is then nothing that could say no.
    """
    platform = _platform()
    if platform.vendor != "postgresql":
        return True
    with platform.cursor() as cursor:
        cursor.execute("SELECT to_regclass('core.project') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return True
        cursor.execute("SELECT 1 FROM core.project WHERE pid = %s", [str(platform_project_id)])
        return cursor.fetchone() is not None
