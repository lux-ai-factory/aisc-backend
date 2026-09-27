import importlib
import unittest
from unittest import mock

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase, override_settings

SEAN_LAST = ("aisc_backend", "0014_ai_system_and_project_config_squashed")
OUR_LAST = ("aisc_backend", "0025_engine_deployment_marker")


class OneHistory(TransactionTestCase):
    def test_the_graph_has_one_leaf_and_seans_0014_is_in_it(self):
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes("aisc_backend")
        self.assertEqual(len(leaves), 1, leaves)
        self.assertIn(SEAN_LAST, executor.loader.graph.forwards_plan(leaves[0]))

    def test_our_migrations_come_after_seans_0014(self):
        graph = MigrationExecutor(connection).loader.graph
        self.assertEqual(graph.leaf_nodes("aisc_backend"), [OUR_LAST])
        plan = graph.forwards_plan(OUR_LAST)
        self.assertLess(plan.index(SEAN_LAST), plan.index(("aisc_backend", "0015_plugin_catalogue_slug")))

    def test_the_tables_lose_the_prefix(self):
        tables = connection.introspection.table_names()
        for table in ("project", "plugin", "evaluation", "ai_system", "ai_component", "project_config"):
            self.assertIn(table, tables)
        self.assertNotIn("aisc_backend_project", tables)

    def test_a_standalone_database_keeps_its_rows_through_our_migrations(self):
        executor = MigrationExecutor(connection)
        executor.migrate([SEAN_LAST])
        old = executor.loader.project_state([SEAN_LAST]).apps
        Project = old.get_model("aisc_backend", "Project")
        Plugin = old.get_model("aisc_backend", "Plugin")
        project = Project.objects.create(name="MCAS-free example", status="Ready")
        Plugin.objects.create(project=project, package_name="aisc-plugin-langbite", version="0.1.1",
                              display_name="LangBiTe")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes("aisc_backend"))
        new = executor.loader.project_state(executor.loader.graph.leaf_nodes("aisc_backend")).apps
        self.assertEqual(new.get_model("aisc_backend", "Project").objects.count(), 1)
        self.assertEqual(new.get_model("aisc_backend", "Plugin").objects.count(), 1)

    @unittest.skipUnless(settings.AISC_DEPLOYMENT == "standalone", "standalone run only")
    def test_standalone_keeps_the_login_tables(self):
        # the configurator-only table drop (0023) must not run here
        tables = connection.introspection.table_names()
        self.assertIn("auth_user", tables)


class NoLoginOfItsOwn(SimpleTestCase):
    """0023 drops the login tables only in configurator mode."""

    def _run_forwards(self):
        migration = importlib.import_module("aisc_backend.migrations.0023_no_login_of_its_own")
        schema_editor = mock.MagicMock()
        schema_editor.quote_name = lambda name: f'"{name}"'
        cursor = schema_editor.connection.cursor.return_value.__enter__.return_value
        migration.forwards(None, schema_editor)
        return [call.args[0] for call in cursor.execute.call_args_list]

    @override_settings(AISC_DEPLOYMENT="standalone")
    def test_standalone_drops_nothing(self):
        self.assertEqual(self._run_forwards(), [])

    @override_settings(AISC_DEPLOYMENT="configurator")
    def test_configurator_drops_the_login_tables(self):
        statements = self._run_forwards()
        self.assertIn('DROP TABLE IF EXISTS "auth_user"', statements)
        self.assertIn('DROP TABLE IF EXISTS "django_session"', statements)
