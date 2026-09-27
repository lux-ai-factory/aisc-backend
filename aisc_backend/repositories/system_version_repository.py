"""The latest saved AI card version of a platform project (core.system), for the test stamp."""
import logging
import uuid

from asgiref.sync import sync_to_async
from django.db import DatabaseError, connection, transaction

logger = logging.getLogger(__name__)
LATEST = "SELECT pid FROM core.system WHERE project_id = %s ORDER BY number DESC LIMIT 1"


def latest_system_pid_sync(platform_project_id: uuid.UUID | None) -> uuid.UUID | None:
    """The pid of the project's highest-numbered card version, or None.

    None when the engine project is not linked to the platform, on sqlite, when
    core.system is absent, when the project has no version yet, or when the
    query fails (it runs in a savepoint, so a failure spoils nothing).
    """
    if platform_project_id is None or connection.vendor != "postgresql":
        return None
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('core.system') IS NOT NULL")
                if not cursor.fetchone()[0]:
                    return None
                cursor.execute(LATEST, [str(platform_project_id)])
                row = cursor.fetchone()
    except DatabaseError:
        logger.warning("could not read the latest system version of %s",
                       platform_project_id, exc_info=True)
        return None
    return uuid.UUID(str(row[0])) if row else None


async def latest_system_pid(platform_project_id: uuid.UUID | None) -> uuid.UUID | None:
    return await sync_to_async(latest_system_pid_sync)(platform_project_id)
