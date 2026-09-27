"""What the engine reads of the platform's projects.

One database: the platform owns `core.project`, the engine has SELECT on it and
nothing more. The engine's own project row is not a second project someone
creates; it is this service's side of the platform project it was opened on, so
the only thing it needs from the platform is the name to show.

Absent `core` (the sqlite database the test runner builds, or an engine running
on its own) there is no name to read, and the caller names the row itself.
"""
from __future__ import annotations

from django.db import connection


def platform_project_name(platform_project_id) -> str | None:
    """The platform's name for this project, or None when it cannot be read."""
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('core.project') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(
            "SELECT name FROM core.project WHERE pid = %s", [str(platform_project_id)]
        )
        row = cursor.fetchone()
        return row[0] if row else None
