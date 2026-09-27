from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from aisc_backend import deployment


class TheMode(SimpleTestCase):
    def test_default_is_standalone(self):
        self.assertEqual(deployment.mode({}), deployment.STANDALONE)

    def test_the_two_values(self):
        self.assertEqual(deployment.mode({"AISC_DEPLOYMENT": "standalone"}), "standalone")
        self.assertEqual(deployment.mode({"AISC_DEPLOYMENT": "configurator"}), "configurator")

    def test_case_and_spaces_are_forgiven(self):
        self.assertEqual(deployment.mode({"AISC_DEPLOYMENT": " Configurator "}), "configurator")

    def test_anything_else_stops_the_process_naming_the_variable(self):
        for bad in ("config", "", "prod"):
            with self.assertRaisesRegex(ImproperlyConfigured, "AISC_DEPLOYMENT.*standalone.*configurator"):
                deployment.mode({"AISC_DEPLOYMENT": bad})

    def test_configurator_needs_postgres_for_its_project_databases(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "DB_ENGINE.*postgresql"):
            deployment.check_environment({"AISC_DEPLOYMENT": "configurator",
                                          "DB_ENGINE": "django.db.backends.sqlite3"}, testing=False)
        deployment.check_environment({"AISC_DEPLOYMENT": "configurator",
                                      "DB_ENGINE": "django.db.backends.postgresql"}, testing=False)

    def test_the_test_runner_may_run_configurator_on_one_sqlite_database(self):
        deployment.check_environment({"AISC_DEPLOYMENT": "configurator",
                                      "DB_ENGINE": "django.db.backends.sqlite3"}, testing=True)

    def test_project_databases_only_in_configurator_on_postgres(self):
        pg = "django.db.backends.postgresql"
        self.assertFalse(deployment.project_databases({"DB_ENGINE": pg}))
        self.assertTrue(deployment.project_databases({"AISC_DEPLOYMENT": "configurator", "DB_ENGINE": pg}))
        self.assertFalse(deployment.project_databases({"AISC_DEPLOYMENT": "configurator",
                                                       "DB_ENGINE": "django.db.backends.sqlite3"}))
