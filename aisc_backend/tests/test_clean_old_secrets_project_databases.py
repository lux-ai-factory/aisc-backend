"""`manage.py clean_old_secrets count|apply` on project databases, as a live install runs it.

The SQLite cases (test_clean_old_secrets_command.py) run with project databases off, so no database router is
installed. A live install runs the engine on one Postgres database per project, with the project router that
refuses any ORM query no project is admitted for. Here the real command runs in a child process, with the
engine's deployed environment, against a throwaway Postgres: `count` reports and changes nothing, `apply` writes
the planted rows of the project database.

Needs the throwaway Postgres of test_isolation_engine_db.py (same environment, same set-up; see its docstring):
ENGINE_TEST_SUPERUSER_URL, PLATFORM_TEST_DATABASE_URL, AISC_DEPLOYMENT=configurator and the engine's DB_* pointed
at it. Without them every case here skips. Never the running stack (port 5432 is refused).
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timezone

from django.conf import settings

from aisc_backend.tests.isolation_support import (BACKEND, SUPERUSER_URL_VAR, Cluster, configurator_only,
                                                  database_name, superuser_url)

KEY = "lb-judge-0123456789abcdef"
STRONG = "planted-strong-0123456789"
SHAPED = "sk-proj-abcdefghijklmnopqrstuv"
PL = "6a1c2b3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e"
SKIP_REASON = (f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it "
               "(engine suite)")

_WORLD: dict | None = None
_CLUSTERS: list = []


def _manage(*args) -> subprocess.CompletedProcess:
    """`manage.py <args>` in a child process with this process's (deployed) environment."""
    env = dict(os.environ)
    env.setdefault("RUN_TICKET_KEY", settings.RUN_TICKET_KEY)
    env.setdefault("AUTH_ENABLED", "true")
    return subprocess.run([sys.executable, "manage.py", *args], cwd=BACKEND, env=env, capture_output=True,
                          text=True, timeout=600)


def _configured(project_pid: str, config: dict) -> dict:
    return {"event_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()), "action": "engine.plugin.configured",
            "item_type": "plugin", "item_id": PL, "details": {},
            "content": {"config_id": 1, "config": config, "settings": {}}}


def _seed(pid: str) -> dict:
    """An outbox row, a stored configuration and a failure text holding secrets, through the ORM with the
    project's alias admitted (as the engine writes them)."""
    from django.db import connections

    from aisc_backend import projectdb
    from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, LedgerOutbox, Plugin,
                                     PluginConfig, Project)

    alias = projectdb.open_alias(projectdb.alias_for(pid))
    token = projectdb.admitted.set(alias)
    try:
        project = Project.objects.create(name=f"secrets {pid[:8]}", description="", platform_project_id=pid)
        langbite = Plugin.objects.create(project=project, name="LangBiTe", display_name="LangBiTe",
                                         package_name="aisc-plugin-langbite", version="0.2.8", pid=PL)
        other = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                      version="0.1.0")
        outbox = LedgerOutbox.objects.create(project_pid=pid, delivered_at=datetime.now(timezone.utc),
                                             event=_configured(pid, {"judge_api_key": KEY, "languages": ["en"]}))
        config = PluginConfig.objects.create(plugin=other, config={"api_key": STRONG, "model": "gpt-4o-mini"})
        langbite_config = PluginConfig.objects.create(plugin=langbite, config={"languages": ["en"]})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Failed, project=project)
        run = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=langbite_config,
                                              error_message=f"Incorrect API key provided: {SHAPED}")
        return {"alias": alias, "outbox": outbox.id, "config": config.id, "run": run.id}
    finally:
        projectdb.admitted.reset(token)
        connections[alias].close()


def world(test: unittest.TestCase) -> dict:
    global _WORLD
    if _WORLD is None:
        cluster = Cluster(superuser_url())
        _CLUSTERS.append(cluster)
        pid = cluster.platform_project("secrets", {"alice": "owner"})
        cluster.provision(pid)
        migrated = _manage("migrate_projects")
        if migrated.returncode != 0:
            test.fail(f"manage.py migrate_projects exited {migrated.returncode}: "
                      + (migrated.stderr.strip().splitlines() or ["(no output)"])[-1][:300])
        _WORLD = {"cluster": cluster, "pid": pid, "database": database_name(pid), **_seed(pid)}
    return _WORLD


def tearDownModule():
    for cluster in _CLUSTERS:
        cluster.cleanup()


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class CleanOldSecretsOnProjectDatabases(unittest.TestCase):
    """One project database holding a configuration event with the judge key, a stored configuration with a
    strong-named key, and a failure text with a key shape. The cases run in name order: count, then apply."""

    def setUp(self):
        self.w = world(self)
        self.cluster: Cluster = self.w["cluster"]

    def stored(self):
        db = self.w["database"]
        event = self.cluster.rows(db, "SELECT event FROM engine.aisc_backend_ledgeroutbox WHERE id = %s",
                                  (self.w["outbox"],))[0][0]
        config = self.cluster.rows(db, "SELECT config FROM engine.aisc_backend_pluginconfig WHERE id = %s",
                                   (self.w["config"],))[0][0]
        failure = self.cluster.rows(db, "SELECT error_message FROM engine.aisc_backend_evaluationplugin"
                                        " WHERE id = %s", (self.w["run"],))[0][0]
        return event, config, failure

    def test_1_count_reports_the_project_database_and_changes_nothing(self):
        before = self.stored()
        done = _manage("clean_old_secrets", "count")
        self.assertEqual(done.returncode, 0, done.stderr.strip().splitlines()[-3:])
        db = self.w["alias"]
        lines = done.stdout.splitlines()
        for line in (f"{db}: aisc_backend_ledgeroutbox rows_to_mask=1",
                     f"{db}: aisc_backend_pluginconfig rows_to_change=1 fields_to_drop=1",
                     f"{db}: aisc_backend_evaluationplugin rows_to_scrub=1",
                     f"{db}: rotate plugin=aisc-plugin-a field=api_key configs=1",
                     f"{db}: rotate plugin=aisc-plugin-langbite field=judge_api_key events=1"):
            self.assertIn(line, lines)
        self.assertEqual(self.stored(), before)
        for value in (KEY, STRONG, SHAPED):
            self.assertNotIn(value, done.stdout + done.stderr)

    def test_2_apply_writes_the_project_database(self):
        done = _manage("clean_old_secrets", "apply")
        self.assertEqual(done.returncode, 0, "apply failed on a project database: "
                         + " | ".join(done.stderr.strip().splitlines()[-3:]))
        self.assertNotIn("failed", done.stderr)
        db = self.w["alias"]
        lines = done.stdout.splitlines()
        for line in (f"{db}: aisc_backend_ledgeroutbox rows_masked=1",
                     f"{db}: aisc_backend_pluginconfig rows_changed=1 fields_dropped=1",
                     f"{db}: aisc_backend_evaluationplugin rows_scrubbed=1"):
            self.assertIn(line, lines)
        event, config, failure = self.stored()
        self.assertEqual(event["content"]["config"], {"judge_api_key": "set", "languages": ["en"]})
        self.assertEqual(config, {"model": "gpt-4o-mini"})
        self.assertEqual(failure, "Incorrect API key provided: [redacted]")
        for value in (KEY, STRONG, SHAPED):
            self.assertNotIn(value, done.stdout + done.stderr)

    def test_3_a_count_after_apply_finds_nothing_in_the_project_database(self):
        done = _manage("clean_old_secrets", "count", "--project", self.w["pid"])
        self.assertEqual(done.returncode, 0, done.stderr.strip().splitlines()[-3:])
        db = self.w["alias"]
        self.assertEqual(done.stdout.splitlines(), [
            f"{db}: aisc_backend_ledgeroutbox rows_to_mask=0",
            f"{db}: aisc_backend_pluginconfig rows_to_change=0 fields_to_drop=0",
            f"{db}: aisc_backend_evaluationplugin rows_to_scrub=0",
            "total: rows_to_mask=0 rows_to_change=0 fields_to_drop=0 rows_to_scrub=0"])
