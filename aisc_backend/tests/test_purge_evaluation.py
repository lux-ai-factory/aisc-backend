"""A run's measures, observations and artifacts leave a project database only through the owner-only purge:
`engine.purge_evaluation(evaluation)`, a SECURITY DEFINER function owned by the engine schema's owner role, which
the platform's runtime role may call and the engine's may not. It removes the run's results, marks the run
purged and keeps the run's row, and answers which stored objects the platform must remove next. The engine's
runtime role still cannot delete a measure itself.

Runs in the engine suite's --postgres mode (superuser_url, a throwaway Postgres); without it every case skips.
"""
from __future__ import annotations

import json
import os
import unittest

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, configurator_only, superuser_url  # noqa: F401
from aisc_backend.tests.project_database_world import World, orm

SKIP_REASON = f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it (engine suite)"
PURGE = "SELECT engine.purge_evaluation(%s::uuid)"

_WORLD: World | None = None
_ROWS: dict = {}


def world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World("purge a run")
        _ROWS.update(_seed(_WORLD.pid))
    return _WORLD


def tearDownModule():
    if _WORLD is not None:
        _WORLD.cleanup()


def _seed(pid: str) -> dict:
    from django.utils import timezone

    from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, Measurement, Metric,
                                     Observation, Plugin, PluginConfig, Project)
    from aisc_backend.models.artifact import Artifact

    with orm(pid):
        project = Project.objects.create(name="purge a run", description="", platform_project_id=pid)
        plugin = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                       version="0.1.0")
        config = PluginConfig.objects.create(plugin=plugin, name="saved", config={})
        out = {}
        for key in ("purged", "kept"):
            evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=project)
            test = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config)
            observation = Observation.objects.create(evaluation=evaluation, evaluation_plugin=test, name="o",
                                                     description="", observer="t", tool="t")
            metric, _ = Metric.objects.get_or_create(name="accuracy", defaults={"description": "",
                                                                                "type_spec": "float"})
            Measurement.objects.create(observation=observation, metric=metric, name="accuracy", description="",
                                       score=0.75, time=timezone.now())
            Artifact.objects.create(name="system-under-test-calls.jsonl", description="",
                                    data=f"{key}-calls.jsonl", evaluation_plugin=test)
            out[key] = str(evaluation.pid)
        return out


def _count(w: World, table: str, evaluation: str) -> int:
    joins = {
        "measurement": "engine.aisc_backend_measurement m JOIN engine.aisc_backend_observation o"
                       " ON o.id = m.observation_id",
        "observation": "engine.aisc_backend_observation o",
    }
    return w.scalar(f"SELECT count(*) FROM {joins[table]} JOIN engine.aisc_backend_evaluation e"
                    f" ON e.id = o.evaluation_id WHERE e.pid = %s", (evaluation,))


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class PurgeEvaluation(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def _function(self):
        return self.w.scalar(
            "SELECT (SELECT json_build_object('definer', p.prosecdef, 'owner', pg_get_userbyid(p.proowner))::text"
            " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'engine' AND p.proname = 'purge_evaluation' LIMIT 1)", ())

    def test_the_purge_is_a_definer_function_of_the_schema_owner(self):
        found = self._function()
        self.assertIsNotNone(found, "the project database has no engine.purge_evaluation")
        found = json.loads(found)
        self.assertTrue(found["definer"], "the purge runs with its caller's rights")
        self.assertNotIn(found["owner"], ("engine_rw", "platform_rw"), "the purge is owned by a runtime role")

    def test_the_engines_runtime_role_cannot_delete_a_measure_or_call_the_purge(self):
        self.assertIsNotNone(self.w.refusal("DELETE FROM engine.aisc_backend_measurement", role="engine_rw"),
                             "the engine's runtime role deleted measures")
        self.assertIsNotNone(self.w.refusal(PURGE, role="engine_rw", params=(_ROWS["kept"],)),
                             "the engine's runtime role purged a run")

    def test_the_platform_purges_a_run_and_its_row_stays_marked(self):
        # as platform_rw, which owns the project databases it makes on a live install (here the superuser made it)
        with self.w.connect(autocommit=False) as conn:
            try:
                conn.execute('SET ROLE "platform_rw"')
                answer = conn.execute(PURGE, (_ROWS["purged"],)).fetchone()[0]
            except Exception as exc:  # noqa: BLE001  (the function missing or refused is the finding)
                conn.rollback()
                self.fail(f"the platform cannot purge a run: {type(exc).__name__}: {str(exc).splitlines()[0]}")
            conn.commit()
        self.assertEqual(answer.get("artifacts"), ["purged-calls.jsonl"])
        self.assertEqual(answer.get("measures"), 1)
        self.assertEqual((_count(self.w, "measurement", _ROWS["purged"]),
                          _count(self.w, "observation", _ROWS["purged"])), (0, 0))
        self.assertEqual(self.w.scalar("SELECT count(*) FROM engine.aisc_backend_evaluation WHERE pid = %s"
                                       " AND purged_at IS NOT NULL", (_ROWS["purged"],)), 1)
        self.assertEqual(_count(self.w, "measurement", _ROWS["kept"]), 1, "another run's measures went too")
