"""A project belongs to the people in it.

The engine's project row is this service's side of a platform project. Who may
see it, and who may change it, is the platform's answer: it writes
`core.project_member` in the platform database, this reads it there, through
the `platform` alias (isolation I7.1: the engine's own rows are in the project's
database, membership stays in the platform's). No second copy to disagree with,
and no HTTP call to fail.

Authentication says who is asking; this says what they are to this project.
"""
from __future__ import annotations

import logging

from django.db import DEFAULT_DB_ALIAS, connections
from ninja.errors import HttpError

from aisc_backend.auth import keycloak
from aisc_backend.auth.keycloak import get_roles

logger = logging.getLogger(__name__)

#: The realm role that administers the platform. Not a membership: it is not in
#: the project, it may act on any of them, which is what keeps an orphaned
#: assessment recoverable.
ADMIN_ROLE = "admin"

#: least to most, as in the platform
_RANK = {"viewer": 0, "editor": 1, "owner": 2}


def _platform():
    """The platform database: its own alias on project databases, else the one database."""
    return connections["platform"] if "platform" in connections.settings else connections[DEFAULT_DB_ALIAS]


def role_in_project(platform_project_id, subject: str) -> str | None:
    """What this person is to this platform project, or None.

    None when there is no such membership, and also when there is no `core` to
    read: the sqlite database the test runner builds has none. That is safe
    because the checks below are only made while authentication is on, and a
    deployment with authentication on reads the platform database. A failure to
    read it propagates (the door answers 503).
    """
    if platform_project_id is None or not subject:
        return None
    platform = _platform()
    if platform.vendor != "postgresql":
        return None
    with platform.cursor() as cursor:
        cursor.execute("SELECT to_regclass('core.project_member') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(
            "SELECT role FROM core.project_member WHERE project_id = %s AND subject = %s",
            [str(platform_project_id), subject],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def claims_of(request) -> dict | None:
    """The verified claims on this request, or None when auth is off.

    `request.auth` is what the bearer check left behind: the claims, or True
    when AUTH_ENABLED is False and nothing was verified. Before a view runs there
    is no `request.auth` yet; the door keeps the claims it verified on
    `request.aisc_claims`, and those are read then.
    """
    auth = getattr(request, "auth", None)
    if isinstance(auth, dict):
        return auth
    door = getattr(request, "aisc_claims", None)
    return door if isinstance(door, dict) else None


def role_for(request, platform_project_id) -> str | None:
    """This caller's role in that project, counting the admin role."""
    claims = claims_of(request)
    if claims is None:
        return None
    if ADMIN_ROLE in get_roles(claims):
        return "owner"
    return role_in_project(platform_project_id, claims.get("sub", ""))


def enforced() -> bool:
    """Whether membership is checked at all.

    Off with authentication: there is no verified subject to look up, and a
    development run that had to invent one would be checking nothing anyway.

    Read off the module rather than imported, so the switch is the one thing
    that decides it and a test that flips it flips this too.
    """
    return keycloak.AUTH_ENABLED


def require(request, platform_project_id, needed: str = "viewer") -> str | None:
    """The caller's role, or the refusal.

    404 for "not yours" and 403 for "not enough". A stranger is told what a
    stranger may know: the name of a project is often the name of a customer,
    and 403 would confirm it exists.
    """
    if not enforced():
        return None
    role = role_for(request, platform_project_id)
    if role is None:
        raise HttpError(404, "no such project")
    if _RANK.get(role, -1) < _RANK[needed]:
        raise HttpError(403, f"this takes {needed} on this project")
    return role


# ── resolving the project behind what is not a row of the project's database ──
# A stored file and a Celery task id: the door asks these (project_door, step 8).
# Everything else behind the door is a row of the admitted project's database.


def _require_owner(request, project, needed: str) -> str | None:
    """Membership of the project a row belongs to.

    A row with no project belongs to nobody: nobody reaches it, an admin
    neither, and the answer is 404, the same as for a row that does not exist.
    A project with no platform link has no members: only the realm role
    `admin` reaches it (`role_for` counts that role as owner).
    """
    if not enforced():
        return None
    if project is None:
        raise HttpError(404, "no such thing")
    return require(request, project.platform_project_id, needed)


def for_stored_file(request, container, file_name: str, needed: str = "viewer") -> str | None:
    """A file in object storage, by the name it is stored under.

    The names are uuids, so this was never a guessable address, but it was
    readable by anybody who learned one, and listings hand them out. The row that points at the object is what says whose it is.
    """
    from aisc_backend.models import AIComponent
    from aisc_backend.models.artifact import Artifact
    from aisc_backend.models.common import StorageContainer

    if not enforced():
        return None
    if container in (StorageContainer.Datasets, StorageContainer.Models):
        found = (
            AIComponent.objects.filter(data=file_name, storage_container=container)
            .select_related("system__project")
            .first()
        )
        return _require_owner(request, getattr(getattr(found, "system", None), "project", None), needed)
    artifact = (
        Artifact.objects.filter(data=file_name)
        .select_related("evaluation_plugin__evaluation__project")
        .first()
    )
    evaluation = getattr(getattr(artifact, "evaluation_plugin", None), "evaluation", None)
    return _require_owner(request, getattr(evaluation, "project", None), needed)


def for_task(request, task_pid, needed: str = "viewer") -> str | None:
    """A Celery task id, which the evaluation that started it records."""
    from aisc_backend.models.evaluation import Evaluation

    if not enforced():
        return None
    found = Evaluation.objects.filter(task=task_pid).select_related("project").first()
    return _require_owner(request, found.project if found else None, needed)

