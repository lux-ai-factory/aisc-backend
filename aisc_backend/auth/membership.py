"""A project belongs to the people in it.

The engine's project row is this service's side of a platform project. Who may
see it, and who may change it, is the platform's answer: it writes
`core.project_member`, this reads it in the same database. No second copy to
disagree with, and no HTTP call to fail.

Authentication says who is asking; this says what they are to this project.
"""
from __future__ import annotations

import logging

from django.db import connection
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


def role_in_project(platform_project_id, subject: str) -> str | None:
    """What this person is to this platform project, or None.

    None when there is no such membership, and also when there is no `core` to
    read: the sqlite database the test runner builds has none. That is safe
    because the checks below are only made while authentication is on, and a
    deployment with authentication on has the one database.
    """
    if platform_project_id is None or not subject:
        return None
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cursor:
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
    when AUTH_ENABLED is False and nothing was verified.
    """
    auth = getattr(request, "auth", None)
    return auth if isinstance(auth, dict) else None


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


def may_see(request, platform_project_id) -> bool:
    return not enforced() or role_for(request, platform_project_id) is not None


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


def visible(request, projects):
    """The projects of these that the caller is in."""
    if not enforced():
        return list(projects)
    return [p for p in projects if role_for(request, p.platform_project_id) is not None]


# ── resolving the project behind a child object ──────────────────────────────
# The routes that were missed are the ones addressed by an evaluation, a
# dataset, a model, an artifact, a plugin or a setting. Each of those belongs to
# a project; these find it, so the check is the same one everywhere and no
# route has to know how.


def _require_owner(request, project, needed: str) -> str | None:
    """Membership of the project a row belongs to.

    A row with no project, and a project with no platform link, belong to
    nobody: an admin may still reach them, anybody else is told there is no
    such thing, which is the same answer as for a row that does not exist.
    """
    if not enforced():
        return None
    if project is None:
        raise HttpError(404, "no such thing")
    return require(request, project.platform_project_id, needed)


def for_project_pid(request, project_pid, needed: str = "viewer") -> str | None:
    """By the engine's own project pid, which is what most paths carry."""
    from aisc_backend.models.project import Project

    if not enforced():
        return None
    return _require_owner(
        request, Project.objects.filter(pid=project_pid).first(), needed
    )


def for_evaluation(request, evaluation_pid, needed: str = "viewer") -> str | None:
    from aisc_backend.models.evaluation import Evaluation

    if not enforced():
        return None
    found = Evaluation.objects.filter(pid=evaluation_pid).select_related("project").first()
    return _require_owner(request, found.project if found else None, needed)


def for_component(request, component_pid, needed: str = "viewer") -> str | None:
    """A part of a project's AI system: a dataset, a model, a datashape, an LLM.

    Since the AISystem merge these are one kind of row, and they reach their
    project through the system they belong to.
    """
    from aisc_backend.models import AIComponent

    if not enforced():
        return None
    found = (
        AIComponent.objects.filter(pid=component_pid)
        .select_related("system__project")
        .first()
    )
    return _require_owner(request, found.system.project if found else None, needed)


def for_plugin(request, plugin_pid, needed: str = "viewer") -> str | None:
    from aisc_backend.models import Plugin

    if not enforced():
        return None
    found = Plugin.objects.filter(pid=plugin_pid).select_related("project").first()
    return _require_owner(request, found.project if found else None, needed)


def for_project_config(request, project_pid, config_pid, needed: str = "viewer") -> str | None:
    """A project's config, addressed by the project in the path and its own pid.

    Membership of that project, and the config must be one of its own: a
    member of one project naming another project's config pid is told there is
    no such thing, the same answer as for a pid that does not exist.
    """
    from aisc_backend.models.project_config import ProjectConfig

    role = for_project_pid(request, project_pid, needed)
    if not enforced():
        return role
    if not ProjectConfig.objects.filter(pid=config_pid, project__pid=project_pid).exists():
        raise HttpError(404, "no such thing")
    return role


def for_stored_file(request, container, file_name: str, needed: str = "viewer") -> str | None:
    """A file in object storage, by the name it is stored under.

    The names are uuids, so this was never a guessable address, but it was
    readable by anybody who learned one, and the listings above handed them
    out. The row that points at the object is what says whose it is.
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
        return _require_owner(request, found.system.project if found else None, needed)
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


def visible_by(request, rows, project_of):
    """The rows whose project the caller is in.

    `visible` above takes projects; this takes anything that has one, so a
    listing of evaluations filters the same way a listing of projects does
    rather than refusing outright.
    """
    if not enforced():
        return list(rows)
    keep = []
    for row in rows:
        project = project_of(row)
        if project is not None and role_for(request, project.platform_project_id) is not None:
            keep.append(row)
    return keep
