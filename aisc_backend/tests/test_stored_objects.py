"""The platform deletes a project's files from the engine's shared buckets when it deletes the project, and only
the project's database says which files are its: `engine.stored_objects()` names them, (bucket, name) for every
component file and every run artifact. A SECURITY DEFINER function of the engine schema's owner, which the
platform's runtime role may call.

Runs in the engine suite's --postgres mode (superuser_url, a throwaway Postgres); without it every case skips.
"""
from __future__ import annotations

import os
import unittest

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, configurator_only
from aisc_backend.tests.project_database_world import World, orm

SKIP_REASON = f"needs a throwaway Postgres: set {SUPERUSER_URL_VAR} and the engine's DB_* to it (engine suite)"

_WORLD: World | None = None


def world() -> World:
    global _WORLD
    if _WORLD is None:
        _WORLD = World("stored files")
        _seed(_WORLD.pid)
    return _WORLD


def tearDownModule():
    if _WORLD is not None:
        _WORLD.cleanup()


def _seed(pid: str) -> None:
    from aisc_backend.models import (AIComponent, AIComponentType, Evaluation, EvaluationPlugin, EvaluationStatus,
                                     Plugin, PluginConfig, Project)
    from aisc_backend.models.ai_system import AISystem
    from aisc_backend.models.artifact import Artifact
    from aisc_backend.models.common import StorageContainer

    with orm(pid):
        project = Project.objects.create(name="stored files", description="", platform_project_id=pid)
        system = AISystem.objects.create(project=project, name="sys", description="")
        AIComponent.objects.create(component_type=AIComponentType.DATASET, system=system, name="d.csv",
                                   description="", data="dataset-file.csv",
                                   storage_container=StorageContainer.Datasets)
        AIComponent.objects.create(component_type=AIComponentType.LLM, system=system, name="no file",
                                   description="", data="")
        plugin = Plugin.objects.create(project=project, name="A", display_name="A", package_name="aisc-plugin-a",
                                       version="0.1.0")
        config = PluginConfig.objects.create(plugin=plugin, name="saved", config={})
        evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=project)
        test = EvaluationPlugin.objects.create(evaluation=evaluation, plugin_config=config)
        Artifact.objects.create(name="chart.png", description="", data="run-chart.png", evaluation_plugin=test)


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
@configurator_only
class StoredObjects(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def test_the_platform_reads_every_file_of_the_project(self):
        with self.w.connect(autocommit=False) as conn:
            try:
                conn.execute('SET ROLE "platform_rw"')
                rows = sorted(conn.execute("SELECT bucket, name FROM engine.stored_objects()").fetchall())
            except Exception as exc:  # noqa: BLE001  (the function missing or refused is the finding)
                conn.rollback()
                self.fail(f"the platform cannot list the project's files: {type(exc).__name__}: "
                          f"{str(exc).splitlines()[0]}")
            conn.rollback()
        self.assertEqual(rows, [("artifacts", "run-chart.png"), ("datasets", "dataset-file.csv")])

    def test_it_is_a_definer_function_of_the_schema_owner(self):
        owner = self.w.scalar("SELECT (SELECT pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n"
                              " ON n.oid = p.pronamespace WHERE n.nspname = 'engine' AND p.proname = 'stored_objects'"
                              " AND p.prosecdef LIMIT 1)")
        self.assertNotIn(owner, (None, "engine_rw", "platform_rw"))
