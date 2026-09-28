"""The door: which project's database a request may open (isolation 2026-09-25, I7.2, I7.3).

Every engine table lives in the database of one platform project. A call to
`/api/*` therefore names its project in `X-AISC-Project`, and this middleware
decides, before any view runs, whether the caller may enter that project; only
then does it open the project's database and admit it for the request
(`projectdb.admitted`), so every ORM query of the view goes there and nowhere
else. What is in another project's database cannot be reached, whatever pid the
caller puts in a path or a body.

The order of the checks is the spec's (I7.2), and a later check never runs
before an earlier one:

1. no header: 400; 2. a header that is not a pid: 404;
3. the worker (`/api/v1/internal/*`): wrong `X-Internal-Secret` 401; no
   evaluation named in `X-AISC-Evaluation`, no run ticket in `X-AISC-Run`, a
   ticket that is not the one minted for this project and this evaluation, or a
   path naming another evaluation: 403;
4. a person (only while authentication is on): no or a bad token 401; the
   membership cannot be read 503; a stranger 404; a viewer writing 403. The
   realm role `admin` is not a membership and skips the lookup. The claims
   verified here are kept on `request.aisc_claims`;
4a. `POST /api/v1/projects`: 403, projects are made on the Configurator's
   launcher; installing, removing or refreshing a plugin without the realm
   role `admin` (while authentication is on): 403;
5. the alias of the project's database is registered (no connection yet);
6. a pid the platform has no project for: 404 (503 when that cannot be read);
7. the database is opened, and migrated the first time this process opens it:
   a missing database 404 (I2.5), a failed migration 503;
8. the alias is admitted, and every pid of a path or a body that names an
   engine object must be one of this database's, else 404. A stored file and a
   Celery task are not rows of the database: the row that records them must be
   one of this database's and the caller a member of its project
   (`membership.for_stored_file`, `membership.for_task`), else the same refusal;
9. the view.

The unauthenticated and project-less routes (docs, openapi, app-name, me,
me/admin, audit, `GET /plugins`) pass through untouched, except that the
gateway's token is copied into `Authorization` first (`bearer_from_gateway`).
At the end of the request the admitted alias is released and the project's and
the platform's connections are closed in the thread that used them
(CONN_MAX_AGE=0, I17.1).

The door names nothing of the platform's schema: membership and the project's
existence are asked of `auth.membership` and `platform_projects`.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import uuid

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.db import DatabaseError, connections
from django.http import JsonResponse
from ninja.errors import HttpError

from aisc_backend import platform_projects, projectdb
from aisc_backend.auth import keycloak, membership

PROJECT_HEADER = "X-AISC-Project"
RUN_HEADER = "X-AISC-Run"
EVALUATION_HEADER = "X-AISC-Evaluation"
SECRET_HEADER = "X-Internal-Secret"
INTERNAL_PREFIX = "/api/v1/internal/"
#: The header oauth2-proxy sets when it has vouched for a request. In the Configurator the
#: gateway holds the session, refreshes it, and knows who is behind the request; a page behind it
#: therefore need not keep a second session of its own, and when it does not, this is where the
#: token arrives. The door copies it into `Authorization`, so Sean's bearer check reads it.
GATEWAY_TOKEN_HEADER = "X-Auth-Request-Access-Token"

#: Routes that need no project, whatever the method (plan 4's list).
EXEMPT = {"/api/docs", "/api/openapi.json", "/api/v1/app/app-name", "/api/v1/me", "/api/v1/me/admin",
          "/api/v1/audit"}
#: Routes that need no project for these methods only: the list of installable
#: plugins. Installing one (POST) is recorded in a project, so it needs one.
EXEMPT_FOR = {("GET", "/api/v1/plugins"), ("HEAD", "/api/v1/plugins")}

#: POST routes that only read (plan 4's list): a viewer may call them.
READ_POSTS = re.compile(
    r"^/api/v1/(?:evaluations/[^/]+/measurements/(?:aggregate|dimension-keys|metric-names|dimension-values/[^/]+)"
    r"|projects/[^/]+/measurements/aggregate|plugins/[^/]+/config/state)$")

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ENGINE_PROJECT_PATH = re.compile(rf"^/api/v1/(?:projects|stats/projects|project/settings)/({_UUID})(?:/|$)")
_EVALUATION_PATH = re.compile(rf"^/api/v1/evaluations/({_UUID})(?:/|$)")
#: Sean's `GET /components/{pid}/data` answers 500 for any failure, a missing row included.
_COMPONENT_PATH = re.compile(rf"^/api/v1/components/({_UUID})(?:/|$)")
_RESULT_PATH = re.compile(rf"^/api/v1/plugins/[^/]+/evaluations/({_UUID})/result$")
_FOR_PLATFORM_PATH = re.compile(r"^/api/v1/projects/for-platform/([^/]+)$")
_PROJECT_ROW_PATH = re.compile(rf"^/api/v1/projects/{_UUID}$")
_INTERNAL_EVALUATION_PATH = re.compile(r"^/api/v1/internal/evaluations/([^/]+)(?:/|$)")
_INTERNAL_SETTINGS_PATH = re.compile(rf"^/api/v1/internal/projects/settings/({_UUID})(?:/by-pid)?$")
_INTERNAL_FILE_PATH = re.compile(r"^/api/v1/internal/files/(dataset|model)/([^/]+)$")
_FILE_PATH = re.compile(r"^/api/v1/files/(dataset|model|artifact)/([^/]+)$")
#: Sean's task router is mounted as `/tasks` with the route `{pid}/status`: both spellings.
_TASK_PATH = re.compile(r"^/api/v1/tasks/?([^/]+)/status$")

#: Installing, removing or refreshing a plugin fetches a distribution and the engine then runs
#: its code on a shared server, so in the Configurator it takes the realm role `admin`.
_ADMIN_ROUTES = {("POST", "/api/v1/plugins"), ("DELETE", "/api/v1/plugins"), ("POST", "/api/v1/plugins/refresh")}

#: JSON bodies that name an engine project, by (method, path): the field.
_BODY_ENGINE_PROJECT = {
    ("POST", "/api/v1/plugins"): "project_uuid",
    ("DELETE", "/api/v1/plugins"): "project_uuid",
    ("POST", "/api/v1/plugins/refresh"): "project_uuid",
    ("POST", "/api/v1/evaluations/task"): "project_pid",
}


def writes(method: str, path: str) -> bool:
    """Whether this call changes something (a viewer may not make it)."""
    return method.upper() not in ("GET", "HEAD", "OPTIONS") and not READ_POSTS.match(path)


def _exempt(method: str, path: str) -> bool:
    return path in EXEMPT or (method.upper(), path) in EXEMPT_FOR


def _refuse(status: int, detail: str) -> JsonResponse:
    return JsonResponse({"detail": detail}, status=status)


def _same_pid(value, pid: str) -> bool:
    try:
        return projectdb.normalise(value) == pid
    except projectdb.NotAPid:
        return False


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _json_body(request):
    """The JSON object of this request, or None (a body that does not parse is the view's 422)."""
    if "json" not in (request.content_type or ""):
        return None
    try:
        body = json.loads(request.body or b"null")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def bearer_from_gateway(request) -> None:
    """The gateway's token as the bearer, when the page sent no `Authorization` of its own.

    Both are the same session and are verified the same way; a request carrying
    neither is still refused.
    """
    if request.META.get("HTTP_AUTHORIZATION"):
        return
    token = request.META.get("HTTP_" + GATEWAY_TOKEN_HEADER.upper().replace("-", "_"), "").strip()
    if token:
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        # `request.headers` is a copy of META made once; drop it so every reader sees the bearer.
        request.__dict__.pop("headers", None)


def _bearer(request) -> str:
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return ""


def _worker_refusal(request, pid: str, path: str):
    """Step 3: the worker's secret, ticket and evaluation."""
    expected = os.environ.get("INTERNAL_API_KEY") or ""
    given = request.headers.get(SECRET_HEADER) or ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        return _refuse(401, "the internal secret is wrong")
    named = request.headers.get(EVALUATION_HEADER)
    ticket = request.headers.get(RUN_HEADER)
    try:
        evaluation = projectdb.normalise(named)
    except projectdb.NotAPid:
        return _refuse(403, "an internal call names its evaluation")
    if not ticket:
        return _refuse(403, "an internal call carries its run ticket")
    if not projectdb.ticket_is_valid(pid, evaluation, ticket):
        return _refuse(403, "the run ticket is not for this project and evaluation")
    in_path = _INTERNAL_EVALUATION_PATH.match(path)
    if in_path and not _same_pid(in_path.group(1), evaluation):
        return _refuse(403, "the path names another evaluation than the ticket")
    return None


async def _person_refusal(request, pid: str, path: str):
    """Step 4: who the caller is, and what they are to this project."""
    if not keycloak.AUTH_ENABLED:
        return None
    token = _bearer(request)
    if not token:
        return _refuse(401, "sign in")
    try:
        claims = keycloak.verify_token(token)
    except Exception:  # noqa: BLE001 - any failure to verify is "not signed in"
        return _refuse(401, "sign in")
    request.aisc_claims = claims or {}
    if membership.ADMIN_ROLE in keycloak.get_roles(claims or {}):
        return None
    try:
        role = await sync_to_async(membership.role_in_project)(pid, (claims or {}).get("sub", ""))
    except DatabaseError:
        return _refuse(503, "the platform's memberships cannot be read")
    if role is None:
        return _refuse(404, "no such project")
    if role == "viewer" and writes(request.method, path):
        return _refuse(403, "this takes editor on this project")
    return None


def _rule_refusal(request, method: str, path: str):
    """Step 4a: what the Configurator does not let anybody do here, or not without admin."""
    if method == "POST" and path == "/api/v1/projects":
        return _refuse(403, "projects are made on the Configurator's launcher")
    if (method, path) in _ADMIN_ROUTES and keycloak.AUTH_ENABLED:
        claims = getattr(request, "aisc_claims", None) or {}
        if membership.ADMIN_ROLE not in keycloak.get_roles(claims):
            return _refuse(403, f"this needs the {membership.ADMIN_ROLE!r} role")
    return None


def _outside_the_database(request, path: str):
    """Step 8, for what is not a row: a stored file, a Celery task (runs with the alias admitted).

    The row that records it says whose it is; `membership` raises the refusal.
    """
    from aisc_backend.models.common import StorageContainer

    containers = {"dataset": StorageContainer.Datasets, "model": StorageContainer.Models,
                  "artifact": StorageContainer.Artifacts}
    try:
        stored = _FILE_PATH.match(path)
        if stored:
            membership.for_stored_file(request, containers[stored.group(1)], stored.group(2))
        task = _TASK_PATH.match(path)
        if task and _as_uuid(task.group(1)) is not None:  # not a uuid: the view's 422
            membership.for_task(request, _as_uuid(task.group(1)))
    except HttpError as refused:
        return _refuse(refused.status_code, refused.message)
    return None


def _not_in_this_database(request, pid: str, path: str, method: str, internal: bool) -> bool:
    """Step 8: a pid in the path or body that names an engine object must be
    one of the admitted database's (runs with the alias admitted)."""
    from aisc_backend.models import AIComponent, Project
    from aisc_backend.models.common import StorageContainer
    from aisc_backend.models.evaluation import Evaluation

    def project_here(value) -> bool:
        found = _as_uuid(value)
        return found is not None and Project.objects.filter(pid=found).exists()

    def evaluation_here(value) -> bool:
        found = _as_uuid(value)
        return found is not None and Evaluation.objects.filter(pid=found).exists()

    if internal:
        if not evaluation_here(request.headers.get(EVALUATION_HEADER)):
            return True
        settings_path = _INTERNAL_SETTINGS_PATH.match(path)
        if settings_path and not project_here(settings_path.group(1)):
            return True
        file_path = _INTERNAL_FILE_PATH.match(path)
        if file_path:
            container = StorageContainer.Datasets if file_path.group(1) == "dataset" else StorageContainer.Models
            if not AIComponent.objects.filter(data=file_path.group(2), storage_container=container).exists():
                return True
        return False

    for_platform = _FOR_PLATFORM_PATH.match(path)
    if method == "POST" and for_platform and not _same_pid(for_platform.group(1), pid):
        return True

    if (method == "POST" and path == "/api/v1/projects") or (method == "PATCH" and _PROJECT_ROW_PATH.match(path)):
        body = _json_body(request)
        linked = body.get("platform_project_id") if body else None
        if linked is not None and not _same_pid(linked, pid):
            return True

    field = _BODY_ENGINE_PROJECT.get((method, path))
    if field:
        body = _json_body(request)
        if body is not None and _as_uuid(body.get(field)) is not None and not project_here(body.get(field)):
            return True

    project_path = _ENGINE_PROJECT_PATH.match(path)
    if project_path and not project_here(project_path.group(1)):
        return True
    evaluation_path = _EVALUATION_PATH.match(path) or _RESULT_PATH.match(path)
    if evaluation_path and not evaluation_here(evaluation_path.group(1)):
        return True
    component_path = _COMPONENT_PATH.match(path)
    if component_path and not AIComponent.objects.filter(pid=component_path.group(1)).exists():
        return True
    return False


def _close(alias: str | None) -> None:
    """Close this thread's connections to the project's and the platform's database."""
    if alias and projectdb.DATABASE.match(alias) and alias in connections.settings:
        connections[alias].close()
    if projectdb.enabled() and "platform" in connections.settings:
        connections["platform"].close()


class ProjectDoor:
    """Async middleware: admits one project's database per `/api/*` request."""

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        bearer_from_gateway(request)
        path = request.path_info
        method = (request.method or "GET").upper()
        if not path.startswith("/api/") or _exempt(method, path):
            return await self.get_response(request)

        header = request.headers.get(PROJECT_HEADER)
        if not header:
            return _refuse(400, f"name the project in {PROJECT_HEADER}")
        try:
            pid = projectdb.normalise(header)
        except projectdb.NotAPid:
            return _refuse(404, "no such project")

        internal = path.startswith(INTERNAL_PREFIX)
        alias = None
        admitted = admitted_pid = None
        try:
            refusal = (_worker_refusal(request, pid, path) if internal
                       else await _person_refusal(request, pid, path))
            if refusal is not None:
                return refusal
            if not internal:
                refusal = _rule_refusal(request, method, path)
                if refusal is not None:
                    return refusal

            try:
                alias = projectdb.alias_for(pid)
            except projectdb.NotAPid:
                return _refuse(404, "no such project")
            except DatabaseError:
                return _refuse(503, "the project's database cannot be reached")

            try:
                exists = await sync_to_async(platform_projects.platform_project_exists)(pid)
            except DatabaseError:
                return _refuse(503, "the platform's projects cannot be read")
            if not exists:
                projectdb.forget(alias)
                return _refuse(404, "no such project")

            if projectdb.enabled():
                try:
                    await sync_to_async(projectdb.open_alias)(alias)
                except (projectdb.NoSuchProjectDatabase, projectdb.SchemaNotProvisioned):
                    projectdb.forget(alias)
                    return _refuse(404, "no such project")
                except (projectdb.MigrationFailed, DatabaseError):
                    return _refuse(503, "the project's database cannot be opened")

            admitted = projectdb.admitted.set(alias)
            admitted_pid = projectdb.admitted_pid.set(pid)
            try:
                elsewhere = await sync_to_async(_not_in_this_database)(request, pid, path, method, internal)
            except DatabaseError:
                return _refuse(503, "the project's database cannot be read")
            if elsewhere:
                return _refuse(404, "no such thing in this project")
            if not internal and method in ("GET", "HEAD"):
                try:
                    refusal = await sync_to_async(_outside_the_database)(request, path)
                except DatabaseError:
                    return _refuse(503, "the project's database cannot be read")
                if refusal is not None:
                    return refusal

            return await self.get_response(request)
        finally:
            if admitted_pid is not None:
                projectdb.admitted_pid.reset(admitted_pid)
            if admitted is not None:
                projectdb.admitted.reset(admitted)
            await sync_to_async(_close)(alias)
