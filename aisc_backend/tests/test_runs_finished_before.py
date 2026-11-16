"""The platform's retention sweep asks the project's database which runs ended before a moment:
`engine.runs_finished_before(moment)` answers the pid of every run that is done, failed or archived, not purged
yet, whose last test ended (or which was made, when no test says) before it. A SECURITY DEFINER function of the
engine schema's owner, which the platform's runtime role may call.

Runs in the engine suite's --postgres mode (superuser_url, a throwaway Postgres); without it every case skips.
"""
from __future__ import annotations

import os
import unittest
from datetime import timedelta

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, configurator_only
from aisc_backend.tests.project_database_world import World, orm

SKIP_REASON = f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it (engine suite)"

_WORLD: World | None = None
_RUNS: dict = {}


def world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World("runs that ended")
        _RUNS.update(_seed(_WORLD.pid))
    return _WORLD


def tearDownModule():
    if _WORLD is not None:
        _WORLD.cleanup()


def _seed(pid: str) -> dict:
    from django.utils import timezone

    from aisc_backend.models import Evaluation, EvaluationPlugin, EvaluationStatus, Plugin, PluginConfig, Project

    now = timezone.now()
    with orm(pid):
        project = Project.objects.create(name="runs that ended", description="", platform_project_id=pid)
        plugin = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                       version="0.1.0")
        config = PluginConfig.objects.create(plugin=plugin, name="saved", config={})
        out = {}
        for key, status, ended in (("old", EvaluationStatus.Done, now - timedelta(days=40)),
                                   ("new", EvaluationStatus.Done, now - timedelta(days=5)),
                                   ("running", EvaluationStatus.Processing, now - timedelta(days=40))):
            evaluation = Evaluation.objects.create(status=status, project=project)
            EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config, finished_at=ended)
            out[key] = str(evaluation.pid)
        out["now"] = now
        return out


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class RunsFinishedBefore(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def test_only_ended_runs_older_than_the_moment_are_named(self):
        with self.w.connect(autocommit=False) as conn:
            try:
                conn.execute('SET ROLE "platform_rw"')
                found = [str(r[0]) for r in conn.execute("SELECT engine.runs_finished_before(%s)",
                                                         (_RUNS["now"] - timedelta(days=30),))]
            except Exception as exc:  # noqa: BLE001  (the function missing or refused is the finding)
                conn.rollback()
                self.fail(f"the platform cannot ask which runs ended: {type(exc).__name__}: "
                          f"{str(exc).splitlines()[0]}")
            conn.rollback()
        self.assertEqual(found, [_RUNS["old"]])
