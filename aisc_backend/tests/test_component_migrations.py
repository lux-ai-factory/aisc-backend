"""
The component migrations, exercised on the real schema (ADR 0009).

* The mainline ``llm`` component is a type the upgrade must preserve: 0043 turns
  it into an ``endpoint`` (``openai-chat``, preset ``llm``) and its reverse
  restores it.
* The unshipped unified-app ``Connection``/``Helper`` rows are converted to
  endpoint components and their secrets re-keyed, then the tables are dropped
  (a best-effort conversion is enough, ADR 0003).
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BASE_LLM = ("aisc_backend", "0042_alter_runbinding_evaluation_plugin_and_more")
WITH_ENDPOINTS = ("aisc_backend", "0043_component_endpoint_model")
HEAD = ("aisc_backend", "0044_dissolve_connections_helpers_bindings")


class _MigrationTestCase(TransactionTestCase):
    migrate_from = BASE_LLM

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.old_apps = self.executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        # leave the schema at head for the next test
        self.executor.loader.build_graph()
        self.executor.migrate([HEAD])
        super().tearDown()

    def _migrate_to(self, target):
        self.executor.loader.build_graph()
        self.executor.migrate([target])
        return self.executor.loader.project_state([target]).apps


class LlmComponentMigrationTest(_MigrationTestCase):
    def test_an_llm_component_survives_the_round_trip(self):
        Project = self.old_apps.get_model("aisc_backend", "Project")
        AISystem = self.old_apps.get_model("aisc_backend", "AISystem")
        AIComponent = self.old_apps.get_model("aisc_backend", "AIComponent")

        project = Project.objects.create(name="p", description="", status="Ready")
        system = AISystem.objects.create(project=project, name="sys", description="")
        component = AIComponent.objects.create(
            name="llm", description="", data="", component_type="llm",
            system=system,
            json_value={"endpoint_url": "https://api.example/v1", "secret_key": "sk-name"},
        )

        new_apps = self._migrate_to(WITH_ENDPOINTS)
        migrated = new_apps.get_model("aisc_backend", "AIComponent").objects.get(pk=component.pk)
        self.assertEqual(migrated.component_type, "endpoint")
        self.assertEqual(migrated.protocol, "openai-chat")
        self.assertEqual(migrated.adapter, "openai")
        self.assertEqual(migrated.preset, "llm")
        self.assertEqual(migrated.url, "https://api.example/v1")
        self.assertEqual(migrated.secret_key, "sk-name")

        # and the reverse restores the mainline shape
        back_apps = self._migrate_to(BASE_LLM)
        restored = back_apps.get_model("aisc_backend", "AIComponent").objects.get(pk=component.pk)
        self.assertEqual(restored.component_type, "llm")
        self.assertEqual(restored.json_value["endpoint_url"], "https://api.example/v1")
        self.assertEqual(restored.json_value["secret_key"], "sk-name")


class ConnectionConversionMigrationTest(_MigrationTestCase):
    migrate_from = WITH_ENDPOINTS

    def test_connections_and_helpers_become_endpoint_components(self):
        Project = self.old_apps.get_model("aisc_backend", "Project")
        Connection = self.old_apps.get_model("aisc_backend", "Connection")
        Helper = self.old_apps.get_model("aisc_backend", "Helper")
        ProjectConfig = self.old_apps.get_model("aisc_backend", "ProjectConfig")

        project = Project.objects.create(name="p", description="", status="Ready")
        Connection.objects.create(
            project=project, name="judge", description="", label="Judge",
            kind="openai", base_url="https://api.example/v1", model="gpt-4o-mini",
        )
        Helper.objects.create(
            project=project, name="grader", description="", label="",
            endpoint_url="https://h.example/v1", roles=["judge"],
        )
        ProjectConfig.objects.create(
            project=project, name="connection:judge:key", description="",
            category="secrets", key="connection:judge:key",
            encrypted_value="cipher", masked_value="***",
        )
        ProjectConfig.objects.create(
            project=project, name="helper:grader:key", description="",
            category="secrets", key="helper:grader:key",
            encrypted_value="cipher2", masked_value="***",
        )

        new_apps = self._migrate_to(HEAD)
        AIComponent = new_apps.get_model("aisc_backend", "AIComponent")
        ProjectConfig = new_apps.get_model("aisc_backend", "ProjectConfig")

        components = {c.name: c for c in AIComponent.objects.filter(project_id=project.pk)}
        self.assertEqual(set(components), {"judge", "grader"})
        judge = components["judge"]
        self.assertEqual(judge.component_type, "endpoint")
        self.assertEqual(judge.protocol, "openai-chat")
        self.assertEqual(judge.adapter, "openai")
        self.assertEqual(judge.adapter_config, {"model": "gpt-4o-mini"})
        self.assertEqual(judge.secret_key, f"component:{judge.pid}:key")

        keys = set(ProjectConfig.objects.filter(category="secrets").values_list("key", flat=True))
        self.assertIn(f"component:{judge.pid}:key", keys)
        self.assertIn(f"component:{components['grader'].pid}:key", keys)
        self.assertNotIn("connection:judge:key", keys)

        # the dropped tables are gone
        tables = connection.introspection.table_names()
        self.assertNotIn("aisc_backend_connection", tables)
        self.assertNotIn("aisc_backend_helper", tables)
        self.assertNotIn("aisc_backend_runbinding", tables)
