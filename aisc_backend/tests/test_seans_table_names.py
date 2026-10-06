"""The engine keeps Sean's table names (adapt plan 2026-09-28, item 1): our migrations add
columns and tables, they rename none of his."""
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AlterModelTable
from django.test import TestCase, TransactionTestCase

# The db_table of every model on origin/master (none sets its own, so each is
# Django's default). Written out rather than computed, so a name Sean chose
# himself would be compared as he chose it.
MASTER_TABLES = {
    "AISystem": "aisc_backend_aisystem",
    "AIComponent": "aisc_backend_aicomponent",
    "Artifact": "aisc_backend_artifact",
    "Evaluation": "aisc_backend_evaluation",
    "Measurement": "aisc_backend_measurement",
    "MetricCategory": "aisc_backend_metriccategory",
    "Metric": "aisc_backend_metric",
    "Direct": "aisc_backend_direct",
    "Derived": "aisc_backend_derived",
    "Observation": "aisc_backend_observation",
    "Plugin": "aisc_backend_plugin",
    "PluginConfig": "aisc_backend_pluginconfig",
    "PluginConfigProjectConfig": "aisc_backend_pluginconfigprojectconfig",
    "EvaluationInput": "aisc_backend_evaluationinput",
    "EvaluationPlugin": "aisc_backend_evaluationplugin",
    "Project": "aisc_backend_project",
    "ProjectConfig": "aisc_backend_projectconfig",
}


class SeansTableNames(TestCase):
    def test_every_model_keeps_the_table_name_it_has_on_master(self):
        config = apps.get_app_config("aisc_backend")
        for name, table in MASTER_TABLES.items():
            self.assertEqual(config.get_model(name)._meta.db_table, table, name)

    def test_the_migrated_database_has_his_tables(self):
        tables = set(connection.introspection.table_names())
        for name in ("aisc_backend_plugin", "aisc_backend_project", "aisc_backend_evaluation",
                     "aisc_backend_measurement", "aisc_backend_pluginconfig",
                     "aisc_backend_metriccategory_metrics"):
            self.assertIn(name, tables)
        for short in ("plugin", "project", "evaluation", "measurement", "plugin_config",
                      "metric_category_metrics"):
            self.assertNotIn(short, tables)

    def test_no_migration_renames_a_table(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        for (app, name), migration in loader.disk_migrations.items():
            if app != "aisc_backend":
                continue
            renames = [op for op in migration.operations if isinstance(op, AlterModelTable)]
            self.assertEqual(renames, [], name)

    def test_the_chain_after_seans_0014(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        ours = sorted(n for a, n in loader.disk_migrations if a == "aisc_backend" and n >= "0015")
        self.assertEqual(ours, [
            "0015_plugin_catalogue_slug",
            "0016_evaluation_system_id_project_platform_project_id",
            "0017_one_project_per_platform_project",
            "0018_alter_project_platform_project_id",
            "0019_no_login_of_its_own",
            "0020_the_database_is_the_project",
            "0021_engine_deployment_marker",
            "0022_one_system_target_per_system",
        ])


class UpgradeFromSeansMaster(TransactionTestCase):
    def test_a_database_at_his_0014_upgrades(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("aisc_backend", "0014_ai_system_and_project_config_squashed")])
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes("aisc_backend"))
        self.assertIn("aisc_backend_plugin", connection.introspection.table_names())
