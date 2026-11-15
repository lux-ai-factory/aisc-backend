"""Measures, observations, the ledger outbox and saved plugin configurations are append-only in a project
database. A measure or an observation is never changed or removed, not even by the tables' owner. An outbox event
is written and marked delivered or refused through two functions only, and a mark moves once. A saved
configuration changes only by masking a secret in it (a field dropped or a string redacted), or by its name and
description.

Runs in the engine suite's --postgres mode (superuser_url, a throwaway Postgres); without it every case skips.
"""
from __future__ import annotations

import os
import unittest

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, configurator_only, superuser_url  # noqa: F401
from aisc_backend.tests.project_database_world import World, orm

SKIP_REASON = f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it (engine suite)"
KEY = "lb-0123456789abcdef"

_WORLD: World | None = None
_ROWS: dict = {}


def world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World("append only")
        _ROWS.update(_seed(_WORLD.pid))
    return _WORLD


def tearDownModule():
    if _WORLD is not None:
        _WORLD.cleanup()


def _seed(pid: str) -> dict:
    from django.utils import timezone

    from aisc_backend.models import (Evaluation, EvaluationStatus, LedgerOutbox, Measurement, Metric, Observation,
                                     Plugin, PluginConfig, Project)

    with orm(pid):
        project = Project.objects.create(name="append only", description="", platform_project_id=pid)
        plugin = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                       version="0.1.0")
        config = PluginConfig.objects.create(plugin=plugin, name="saved", config={"api_key": KEY, "tokens": 512})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=project)
        observation = Observation.objects.create(evaluation=evaluation, name="o", description="", observer="t",
                                                 tool="t")
        metric = Metric.objects.create(name="accuracy", description="", type_spec="float")
        measurement = Measurement.objects.create(observation=observation, metric=metric, name="accuracy",
                                                 description="", score=0.75, time=timezone.now())
        outbox = LedgerOutbox.objects.create(project_pid=pid, event={"action": "engine.plugin.configured"})
        return {"config": config.id, "observation": observation.id, "measurement": measurement.id,
                "outbox": outbox.id}


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class AppendOnlyResults(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def assertRefused(self, query, *, role=None, set_role=None, params=(), says=None):
        error = self.w.refusal(query, role=role, set_role=set_role, params=params)
        self.assertIsNotNone(error, f"went through: {query} (as {set_role or role or 'the superuser'})")
        if says:
            self.assertIn(says, error)

    def owner(self, table):
        return self.w.owner_of(table)

    # measures and observations

    def test_the_runtime_role_cannot_change_a_measure(self):
        self.assertRefused("UPDATE engine.aisc_backend_measurement SET score = 0 WHERE id = %s", role="engine_rw",
                           params=(_ROWS["measurement"],))

    def test_the_runtime_role_cannot_remove_a_measure(self):
        self.assertRefused("DELETE FROM engine.aisc_backend_measurement WHERE id = %s", role="engine_rw",
                           params=(_ROWS["measurement"],))

    def test_even_the_owner_cannot_change_a_measure(self):
        self.assertRefused("UPDATE engine.aisc_backend_measurement SET score = 0 WHERE id = %s",
                           set_role=self.owner("aisc_backend_measurement"), params=(_ROWS["measurement"],))

    def test_even_the_owner_cannot_remove_an_observation(self):
        self.assertRefused("DELETE FROM engine.aisc_backend_observation WHERE id = %s",
                           set_role=self.owner("aisc_backend_observation"), params=(_ROWS["observation"],))

    def test_even_the_owner_cannot_truncate_measures(self):
        self.assertRefused("TRUNCATE engine.aisc_backend_measurement",
                           set_role=self.owner("aisc_backend_measurement"))

    # the ledger outbox

    def test_the_runtime_role_cannot_rewrite_an_outbox_event(self):
        self.assertRefused("UPDATE engine.aisc_backend_ledgeroutbox SET event = '{}' WHERE id = %s", role="engine_rw",
                           params=(_ROWS["outbox"],))

    def test_even_the_owner_cannot_rewrite_or_remove_an_outbox_event(self):
        owner = self.owner("aisc_backend_ledgeroutbox")
        self.assertRefused("UPDATE engine.aisc_backend_ledgeroutbox SET event = '{}' WHERE id = %s", set_role=owner,
                           params=(_ROWS["outbox"],))
        self.assertRefused("DELETE FROM engine.aisc_backend_ledgeroutbox WHERE id = %s", set_role=owner,
                           params=(_ROWS["outbox"],))

    def test_the_runtime_role_writes_the_outbox_only_through_its_function(self):
        self.assertRefused("INSERT INTO engine.aisc_backend_ledgeroutbox (project_pid, event, queued_at)"
                           " VALUES (%s, '{}', now())", role="engine_rw", params=(self.w.pid,),
                           says="InsufficientPrivilege")
        error = self.w.refusal("SELECT engine.ledger_outbox_append(%s, '{\"action\": \"x\"}'::jsonb)",
                               role="engine_rw", params=(self.w.pid,))
        self.assertIsNone(error, "the outbox's append function is missing or refused")

    def test_an_outbox_mark_moves_once_through_its_function(self):
        import psycopg

        with self.w.connect("engine_rw") as conn:
            try:
                first = conn.execute("SELECT engine.ledger_outbox_mark(%s, true, NULL)", (_ROWS["outbox"],)).fetchone()[0]
                second = conn.execute("SELECT engine.ledger_outbox_mark(%s, false, 422)",
                                      (_ROWS["outbox"],)).fetchone()[0]
            except psycopg.Error as exc:
                self.fail(f"the outbox's mark function is missing or refused: {type(exc).__name__}")
        self.assertEqual((first, second), (True, False))

    # saved plugin configurations

    def test_a_saved_configuration_may_lose_a_secret_field(self):
        error = self.w.refusal("UPDATE engine.aisc_backend_pluginconfig SET config = config - 'api_key' WHERE id = %s",
                               set_role=self.owner("aisc_backend_pluginconfig"), params=(_ROWS["config"],))
        self.assertIsNone(error, "masking a secret out of a saved configuration was refused")

    def test_a_saved_configuration_value_is_fixed(self):
        self.assertRefused("UPDATE engine.aisc_backend_pluginconfig SET config = jsonb_set(config, '{tokens}', '9')"
                           " WHERE id = %s", set_role=self.owner("aisc_backend_pluginconfig"),
                           params=(_ROWS["config"],), says="fixed")

    def test_a_saved_configuration_cannot_be_removed(self):
        self.assertRefused("DELETE FROM engine.aisc_backend_pluginconfig WHERE id = %s",
                           set_role=self.owner("aisc_backend_pluginconfig"), params=(_ROWS["config"],))

    def test_a_saved_configuration_may_be_renamed(self):
        error = self.w.refusal("UPDATE engine.aisc_backend_pluginconfig SET name = 'renamed', description = 'd'"
                               " WHERE id = %s", role="engine_rw", params=(_ROWS["config"],))
        self.assertIsNone(error, "renaming a saved configuration was refused")
