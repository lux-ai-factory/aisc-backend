"""The latest saved AI card version of a project (project.system), for the test stamp.

Isolation 2026-09-25 (I7.8): each project's card versions are the rows of
`project.system` in the project's own database, so the latest is the highest
number in that table, read on the database the evaluation is written to.
"""
import logging
import uuid

from asgiref.sync import sync_to_async
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, transaction

from aisc_backend import projectdb

logger = logging.getLogger(__name__)
LATEST = "SELECT pid FROM project.system ORDER BY number DESC LIMIT 1"


def latest_system_pid_sync(platform_project_id: uuid.UUID | None, using: str | None = None) -> uuid.UUID | None:
    """The pid of the project's highest-numbered card version, or None.

    `using` is the database alias to read (the one the evaluation is saved to);
    by default the admitted project's. None when the engine project is not
    linked to the platform, on sqlite, when project.system is absent, when the
    project has no version yet, or when the query fails (it runs in a
    savepoint, so a failure spoils nothing).
    """
    if platform_project_id is None:
        return None
    alias = using or projectdb.admitted.get() or DEFAULT_DB_ALIAS
    connection = connections[alias]
    if connection.vendor != "postgresql":
        return None
    try:
        with transaction.atomic(using=alias):
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('project.system') IS NOT NULL")
                if not cursor.fetchone()[0]:
                    return None
                cursor.execute(LATEST)
                row = cursor.fetchone()
    except DatabaseError:
        logger.warning("could not read the latest system version of %s",
                       platform_project_id, exc_info=True)
        return None
    return uuid.UUID(str(row[0])) if row else None


async def latest_system_pid(platform_project_id: uuid.UUID | None) -> uuid.UUID | None:
    return await sync_to_async(latest_system_pid_sync)(platform_project_id)
