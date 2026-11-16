"""The one-off for measures stored before the engine kept trace references: `manage.py
reference_conversation_dimensions count|apply` replaces the conversation text in their dimensions with the
reference to the recorded call. Measures are append-only, so the change goes through
`engine.measurement_dimensions_referenced`, which takes exactly that change and nothing else: the same keys,
and each changed value the reference of the text it replaces. A direct UPDATE stays refused. Runs only with
the owner's yes on a live install; here, on a throwaway.

Runs in the engine suite's --postgres mode (superuser_url, a throwaway Postgres); without it every case skips.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, configurator_only
from aisc_backend.tests.project_database_world import World, manage, orm

SKIP_REASON = f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it (engine suite)"
CHANGE = "SELECT engine.measurement_dimensions_referenced(%s, %s::jsonb)"

_WORLD: World | None = None
_ROWS: dict = {}


def world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World("dimensions referenced")
        _ROWS.update(_seed(_WORLD.pid))
    return _WORLD


def tearDownModule():
    if _WORLD is not None:
        _WORLD.cleanup()


def _seed(pid: str) -> dict:
    from django.utils import timezone

    from aisc_backend.models import (Evaluation, EvaluationPlugin, EvaluationStatus, Measurement, Metric,
                                     Observation, Plugin, PluginConfig, Project)

    with orm(pid):
        project = Project.objects.create(name="dimensions referenced", description="", platform_project_id=pid)
        plugin = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                       version="0.1.0")
        config = PluginConfig.objects.create(plugin=plugin, name="saved", config={})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=project)
        test = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config)
        observation = Observation.objects.create(evaluation=evaluation, evaluation_plugin=test, name="o",
                                                 description="", observer="t", tool="t")
        metric, _ = Metric.objects.get_or_create(name="failed", defaults={"description": "", "type_spec": "float"})
        rows = {}
        for key, dims in (("by_hand", {"concern": "age", "Prompt": "how old are you?"}),
                          ("by_command", {"concern": "gender", "prompt": "what is your name?", "answer": "Bot."})):
            rows[key] = Measurement.objects.create(observation=observation, metric=metric, name="failed",
                                                   description="", score=1.0, time=timezone.now(),
                                                   dimensions=dims).id
        return {"test": str(test.pid), **rows}


def _reference(test: str, text: str) -> str:
    return f"trace:{test}#sha256={hashlib.sha256(text.encode()).hexdigest()}"


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class ReferenceConversationDimensions(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def _dimensions(self, key):
        return self.w.scalar("SELECT dimensions FROM engine.aisc_backend_measurement WHERE id = %s", (_ROWS[key],))

    def test_a_direct_update_is_still_refused(self):
        self.assertIsNotNone(self.w.refusal("UPDATE engine.aisc_backend_measurement SET dimensions = '{}'",
                                            role="engine_rw"))

    def test_the_function_takes_the_reference_and_nothing_else(self):
        test = _ROWS["test"]
        wrong = {"concern": "other", "Prompt": _reference(test, "how old are you?")}
        self.assertIsNotNone(self.w.refusal(CHANGE, role="engine_rw", params=(_ROWS["by_hand"], json.dumps(wrong))),
                             "a dimension that is not a conversation key was changed")
        forged = {"concern": "age", "Prompt": _reference(test, "something else")}
        self.assertIsNotNone(self.w.refusal(CHANGE, role="engine_rw", params=(_ROWS["by_hand"], json.dumps(forged))),
                             "a reference to another text was taken")
        right = {"concern": "age", "Prompt": _reference(test, "how old are you?")}
        with self.w.connect("engine_rw") as conn:
            self.assertTrue(conn.execute(CHANGE, (_ROWS["by_hand"], json.dumps(right))).fetchone()[0])
        self.assertEqual(self._dimensions("by_hand"), right)

    def test_the_command_counts_then_replaces_the_text(self):
        counted = manage("reference_conversation_dimensions", "count", "--project", self.w.pid)
        self.assertEqual(counted.returncode, 0, counted.stderr[-500:])
        self.assertIn("measures_to_reference=", counted.stdout)
        self.assertEqual(self._dimensions("by_command")["prompt"], "what is your name?", "count changed a row")
        applied = manage("reference_conversation_dimensions", "apply", "--project", self.w.pid)
        self.assertEqual(applied.returncode, 0, applied.stderr[-500:])
        stored = self._dimensions("by_command")
        self.assertEqual(stored, {"concern": "gender", "prompt": _reference(_ROWS["test"], "what is your name?"),
                                  "answer": _reference(_ROWS["test"], "Bot.")})
        self.assertNotIn("what is your name?", applied.stdout + applied.stderr)
