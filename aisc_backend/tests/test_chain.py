"""The engine's step of the pipeline chain (scripts/test-pipeline-chain.sh, 03 WP12).

A plain unittest.TestCase, not Django's: then `manage.py test` makes no test
database (F14c). Since the isolation (I19.2) the ORM works on the chain project's own
database, admitted the way the door admits a request (projectdb.alias_for, open_alias,
the `admitted` context), with the deployed settings (DB_NAME=platform names only the
platform database). Skipped unless CHAIN_JSON names the chain's shared state.

Step 4: the engine project of the chain's platform project, a plugin installed
from the catalogue (catalogue_slug), and an evaluation created through the ORM,
which the WP9 signal stamps with the latest card version (v1), with one
observation and one measurement for the dashboard to read.
"""
import json
import os
import unittest
import uuid
from pathlib import Path

from django.test import tag
from django.utils import timezone

CHAIN_JSON = os.environ.get("CHAIN_JSON")


@tag("chain")
@unittest.skipUnless(CHAIN_JSON, "not in the pipeline chain (CHAIN_JSON unset)")
class ChainStep4(unittest.TestCase):

    def test_chain_step4_plugin_and_stamped_evaluation(self):
        from django.db import connections

        from aisc_backend import projectdb

        state = json.loads(Path(CHAIN_JSON).read_text())
        alias = projectdb.open_alias(projectdb.alias_for(state["project_pid"]))
        token = projectdb.admitted.set(alias)
        pid_token = projectdb.admitted_pid.set(projectdb.normalise(state["project_pid"]))
        try:
            self._step4(state)
        finally:
            projectdb.admitted_pid.reset(pid_token)
            projectdb.admitted.reset(token)
            connections[alias].close()

    def _step4(self, state):
        from aisc_backend.models import (
            Direct, Evaluation, EvaluationStatus, Measurement, Observation, Plugin, Project,
            ProjectStatus,
        )

        platform_project = uuid.UUID(state["project_pid"])
        project, _ = Project.objects.get_or_create(
            platform_project_id=platform_project,
            defaults={"name": "chain", "description": "", "status": ProjectStatus.Ready},
        )
        plugin = Plugin.objects.create(
            name="chain-plugin", display_name="Chain plugin", description="",
            package_name="aisc-plugin-chain", version="0.1.0", project=project,
            catalogue_slug="chain-plugin", enabled=True,
        )
        evaluation = Evaluation.objects.create(project=project, status=EvaluationStatus.Done)
        evaluation.refresh_from_db()
        self.assertEqual(str(evaluation.system_id), state["v1_pid"],
                         "the evaluation carries the card version that was the latest when it started")

        observation = Observation.objects.create(
            name="o", description="", observer="chain", tool="chain", evaluation=evaluation)
        metric = Direct.objects.create(name="accuracy", description="", type_spec="direct")
        Measurement.objects.create(
            name="m", description="", time=timezone.now(), score=0.9, unit="ratio",
            observation=observation, metric=metric)

        state = json.loads(Path(CHAIN_JSON).read_text())
        state.update(plugin_id=plugin.id, evaluation_pid=str(evaluation.pid))
        Path(CHAIN_JSON).write_text(json.dumps(state))
