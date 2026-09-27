"""Helpers for the isolation tests (isolation 2026-09-25, 01-specs.md section 7).

Not a test module. Two kinds of help:

- `settings_probe`: imports config.settings in a child process with the
  environment of a deployed engine (Postgres), so the tests can read what the
  settings would be there without a database and without changing this
  process's settings.
- `Cluster`: a throwaway Postgres named by ENGINE_TEST_SUPERUSER_URL (the
  superuser DSN of the throwaway's `platform` database, with init/platform-db.sql,
  init/project-databases.sql, init/report-roles.sql and init/inspector-role.sql
  applied). It makes platform projects and their databases through the
  platform's own `projectdb.provision`, so they get the real template. It refuses
  port 5432 (the running stack) and a DSN without a host.

Nothing here prints a DSN or a password.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse
import unittest

from django.conf import settings

BACKEND = Path(__file__).resolve().parents[2]
#: The monorepo holding platform/ and scripts/ (the platform's provisioning and the live shape).
#: The engine's own repository has neither: point AISC_ISOLATION_REPO at a monorepo checkout.
REPO = Path(os.environ.get("AISC_ISOLATION_REPO") or BACKEND.parents[1])
PLATFORM = REPO / "platform"
LIVE_SHAPE = REPO / "scripts" / "tests" / "fixtures" / "isolation" / "live_shape.sql"

#: I1.8: the example pid of every language's tests.
EXAMPLE_PID = "3f2b8c1e-0d4a-4e7b-9a55-1c2d3e4f5a6b"
EXAMPLE_DB = "project_3f2b8c1e0d4a4e7b9a551c2d3e4f5a6b"

SUPERUSER_URL_VAR = "ENGINE_TEST_SUPERUSER_URL"

#: Isolation is the Configurator's (engine deployment modes, 2026-09-27): a standalone run skips it.
configurator_only = unittest.skipUnless(settings.AISC_DEPLOYMENT == "configurator",
                                        "configurator mode only (AISC_DEPLOYMENT=configurator)")

DEPLOYED_ENV = {
    "AISC_DEPLOYMENT": "configurator",
    "DB_ENGINE": "django.db.backends.postgresql",
    "DB_NAME": "platform",
    "DB_USER": "engine_rw",
    "DB_PASSWORD": "not-a-real-password",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "1",  # nothing listens there: the probe must not need a database
    "DB_SCHEMA": "engine",
}


def database_name(pid) -> str:
    return "project_" + str(pid).lower().replace("-", "")


def settings_probe(code: str, extra_env: dict | None = None) -> dict:
    """Run `code` after django.setup() under the deployed environment.

    `code` must assign a JSON-serialisable value to `out`. Returns
    {"ok": True, "out": ...} or {"ok": False, "error": "<type>: <message>"}.
    """
    script = (
        "import json, django\n"
        "django.setup()\n"
        "try:\n"
        + "".join(f"    {line}\n" for line in code.strip().splitlines())
        + "    print('PROBE' + json.dumps({'ok': True, 'out': out}, default=str))\n"
        "except BaseException as exc:\n"
        "    print('PROBE' + json.dumps({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:300]}))\n"
    )
    env = dict(os.environ)
    env.update(DEPLOYED_ENV)
    env.update(extra_env or {})
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    done = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=120)
    for line in done.stdout.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[len("PROBE"):])
    return {"ok": False, "error": "probe crashed: " + done.stderr.strip().splitlines()[-1][:300]
            if done.stderr.strip() else "probe printed nothing"}


def superuser_url() -> str | None:
    url = os.environ.get(SUPERUSER_URL_VAR)
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname or (parsed.port or 5432) == 5432:
        raise RuntimeError(f"{SUPERUSER_URL_VAR} must name a throwaway Postgres on a port other than 5432")
    return url


def dsn_for(url: str, dbname: str | None = None, user: str | None = None, password: str | None = None) -> str:
    from psycopg.conninfo import make_conninfo

    kwargs = {}
    if dbname:
        kwargs["dbname"] = dbname
    if user:
        kwargs["user"] = user
        kwargs["password"] = password if password is not None else user
    return make_conninfo(url.replace("postgresql+psycopg://", "postgresql://"), **kwargs)


def _platform_on_path():
    if str(PLATFORM) not in sys.path:
        sys.path.insert(0, str(PLATFORM))


class Cluster:
    """Platform projects and their databases on the throwaway, cleaned up after."""

    def __init__(self, url: str):
        self.url = url
        self.pids: list[str] = []
        self.extra_databases: list[str] = []

    def connect(self, dbname: str | None = None, autocommit: bool = True):
        import psycopg

        return psycopg.connect(dsn_for(self.url, dbname), autocommit=autocommit)

    def platform_schema(self):
        """core.* as the platform makes it at start (platform_service.migrate, as
        platform_rw, its owner). Idempotent."""
        import psycopg

        _platform_on_path()
        from platform_service.migrate import migrate

        with psycopg.connect(dsn_for(self.url, user="platform_rw")) as conn:
            migrate(conn)

    def platform_project(self, name: str, members: dict[str, str]) -> str:
        """A core.project row and its members; returns its pid."""
        self.platform_schema()
        pid = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute("INSERT INTO core.project (pid, name, slug) VALUES (%s, %s, %s)",
                         (pid, name, f"iso-eng-{pid[:8]}"))
            for subject, role in members.items():
                conn.execute("INSERT INTO core.project_member (project_id, subject, role) VALUES (%s, %s, %s)",
                             (pid, subject, role))
        self.pids.append(pid)
        return pid

    def provision(self, pid: str) -> str:
        """The project's database with every template file there is (projectdb.provision)."""
        _platform_on_path()
        from platform_service import projectdb

        return projectdb.provision(dsn_for(self.url), pid)

    def bare_database(self) -> str:
        """A project_<hex> database with no template: engine_rw may not connect to it."""
        name = database_name(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
            conn.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
        self.extra_databases.append(name)
        return name

    def scratch_database(self, prefix: str) -> str:
        name = f"{prefix}_{uuid.uuid4().hex[:12]}"
        with self.connect() as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        self.extra_databases.append(name)
        return name

    def drop_database(self, name: str):
        with self.connect() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    def rows(self, dbname: str, query: str, params=()):
        with self.connect(dbname) as conn:
            return conn.execute(query, params).fetchall()

    def cleanup(self):
        for pid in self.pids:
            self.drop_database(database_name(pid))
        for name in self.extra_databases:
            self.drop_database(name)
        if self.pids:
            with self.connect() as conn:
                conn.execute("DELETE FROM core.project_member WHERE project_id = ANY(%s::uuid[])", (self.pids,))
                conn.execute("DELETE FROM core.project WHERE pid = ANY(%s::uuid[])", (self.pids,))


#: The catalog of one schema, as sets that can be compared between databases.
CATALOG_QUERIES = {
    "tables": """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p')""",
    "columns": """
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               coalesce(pg_get_expr(d.adbin, d.adrelid), '')
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped""",
    "constraints": """
        SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid)
        FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %(s)s""",
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = %(s)s""",
    "sequences": """
        SELECT sequencename, data_type::text FROM pg_sequences WHERE schemaname = %(s)s""",
}


def catalog(conn, schema: str) -> dict[str, set]:
    return {kind: {tuple(r) for r in conn.execute(q, {"s": schema}).fetchall()}
            for kind, q in CATALOG_QUERIES.items()}


# ── calling the API as deployed (ASGI, auth on), without Django's test databases ──

ISSUER = "http://keycloak:8080/realms/aisc"
INTERNAL_KEY = "the-internal-key"


class _FakeKey:
    def __init__(self, key):
        self.key = key


class ApiCaller:
    """Signs tokens with a throwaway RSA key and calls the API through Django's
    ASGI test client, with authentication on. Membership is NOT stubbed: the
    door reads core.project_member of the throwaway `platform`.

    Use as `self.api = ApiCaller(); self.addCleanup(self.api.stop)` in setUp.
    """

    _private_key = None

    def __init__(self):
        import unittest.mock as mock

        from cryptography.hazmat.primitives.asymmetric import rsa
        from django.test import AsyncClient

        from aisc_backend.auth import keycloak

        if ApiCaller._private_key is None:
            ApiCaller._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fake_client = mock.Mock(get_signing_key_from_jwt=mock.Mock(
            return_value=_FakeKey(ApiCaller._private_key.public_key())))
        self._patchers = [
            mock.patch.object(keycloak, "AUTH_ENABLED", True),
            mock.patch.object(keycloak, "KEYCLOAK_ISSUER", ISSUER),
            mock.patch.object(keycloak, "_get_jwks_client", return_value=fake_client),
            mock.patch.dict("os.environ", {"INTERNAL_API_KEY": INTERNAL_KEY}),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.client = AsyncClient(raise_request_exception=False)

    def stop(self):
        for patcher in reversed(self._patchers):
            patcher.stop()

    def bearer(self, subject="alice", roles=("primary-user",)) -> str:
        import datetime

        import jwt

        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode({"sub": subject, "preferred_username": subject, "iss": ISSUER, "iat": now,
                            "exp": now + datetime.timedelta(minutes=5),
                            "realm_access": {"roles": list(roles)}},
                           ApiCaller._private_key, algorithm="RS256")
        return f"Bearer {token}"

    def call(self, method: str, path: str, headers: dict, body=None):
        import asyncio

        kwargs = {"headers": headers}
        if isinstance(body, tuple) and body[0] == "multipart":
            from django.core.files.uploadedfile import SimpleUploadedFile

            fields = dict(body[1], file=SimpleUploadedFile("probe.txt", b"probe", "text/plain"))
            if method.upper() == "POST":
                # Django's test client re-encodes POST bodies that are dicts of fields itself;
                # passing already-encoded multipart bytes makes it try `.items()` on bytes.
                kwargs.update(data=fields)
            else:
                from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart

                kwargs.update(data=encode_multipart(BOUNDARY, fields), content_type=MULTIPART_CONTENT)
        elif body is not None:
            kwargs.update(data=body, content_type="application/json")

        async def go():
            return await getattr(self.client, method.lower())(path, **kwargs)

        return asyncio.run(go())

    def as_member(self, method, path, project, subject="alice", body=None, roles=("primary-user",)):
        return self.call(method, path, {"Authorization": self.bearer(subject, roles),
                                        "X-AISC-Project": str(project)}, body)

    def as_worker(self, method, path, project, evaluation, body=None):
        from django.conf import settings
        import hashlib
        import hmac

        ticket = hmac.new(settings.SECRET_KEY.encode(), f"{project}.{evaluation}".encode(),
                          hashlib.sha256).hexdigest()
        return self.call(method, path, {"X-Internal-Secret": INTERNAL_KEY, "X-AISC-Project": str(project),
                                        "X-AISC-Run": ticket, "X-AISC-Evaluation": str(evaluation)}, body)
