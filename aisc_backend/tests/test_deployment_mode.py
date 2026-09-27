import os
import sys

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


class SettingsSource(SimpleTestCase):
    def test_includes_only_present_names(self):
        reader = {"AISC_DEPLOYMENT": "configurator", "DB_ENGINE": "django.db.backends.postgresql"}.get
        self.assertEqual(
            deployment.settings_source(reader),
            {"AISC_DEPLOYMENT": "configurator", "DB_ENGINE": "django.db.backends.postgresql"},
        )

    def test_absent_names_are_omitted_so_mode_still_defaults_to_standalone(self):
        reader = lambda name: None
        self.assertEqual(deployment.settings_source(reader), {})
        self.assertEqual(deployment.mode(deployment.settings_source(reader)), deployment.STANDALONE)

    def test_mode_sees_a_value_the_reader_has_but_os_environ_does_not(self):
        from unittest import mock
        with mock.patch.dict(os.environ):
            os.environ.pop("AISC_DEPLOYMENT", None)  # the configurator run sets it; never print environ
            self.assertFalse("AISC_DEPLOYMENT" in os.environ)
            reader = {"AISC_DEPLOYMENT": "configurator"}.get
            self.assertEqual(deployment.mode(deployment.settings_source(reader)), deployment.CONFIGURATOR)


import unittest

from django.conf import settings
from django.db import connection
from django.test import TestCase, override_settings

_STANDALONE_RUN = settings.AISC_DEPLOYMENT == deployment.STANDALONE


@unittest.skipUnless(_STANDALONE_RUN, "standalone run only")
class WhatEachModeLoads(SimpleTestCase):
    def test_standalone_is_seans_stack(self):
        self.assertEqual(settings.AISC_DEPLOYMENT, "standalone")
        self.assertIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertIn("allauth.account.middleware.AccountMiddleware", settings.MIDDLEWARE)
        self.assertNotIn("aisc_backend.project_door.ProjectDoor", settings.MIDDLEWARE)
        self.assertEqual(settings.DATABASE_ROUTERS, [])
        self.assertFalse(settings.PROJECT_DATABASES)

    def test_standalone_serves_seans_login_urls(self):
        from django.urls import resolve
        self.assertEqual(resolve("/admin/").app_name, "admin")
        resolve("/_allauth/browser/v1/auth/session")


class TheDatabaseRemembersItsMode(TestCase):
    @unittest.skipUnless(_STANDALONE_RUN, "standalone run only")
    def test_a_database_made_standalone_refuses_a_configurator_engine(self):
        with override_settings(AISC_DEPLOYMENT="configurator"):
            with self.assertRaisesRegex(ImproperlyConfigured, "made by a standalone engine"):
                deployment.assert_database_mode(connection)

    @unittest.skipIf(_STANDALONE_RUN, "configurator run only")
    def test_a_database_made_configurator_refuses_a_standalone_engine(self):
        with override_settings(AISC_DEPLOYMENT="standalone"):
            with self.assertRaisesRegex(ImproperlyConfigured, "made by a configurator engine"):
                deployment.assert_database_mode(connection)

    def test_the_mode_it_was_made_in_is_accepted(self):
        deployment.assert_database_mode(connection)

    def test_a_database_without_the_marker_is_not_refused(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM engine_deployment")
        with override_settings(AISC_DEPLOYMENT="configurator"):
            deployment.assert_database_mode(connection)


class TheEngineChecksAtStart(TestCase):
    def _check(self, argv):
        from unittest import mock

        from aisc_backend.apps import AiscBackendConfig
        # close() would end the test's transaction; the check's own close is not what is tested here
        with mock.patch.object(sys, "argv", argv), mock.patch.object(connection, "close"):
            AiscBackendConfig._check_the_database_mode()

    def test_a_server_of_the_other_mode_stops_at_start(self):
        other = deployment.STANDALONE if not _STANDALONE_RUN else deployment.CONFIGURATOR
        with override_settings(AISC_DEPLOYMENT=other):
            with self.assertRaisesRegex(ImproperlyConfigured, "this database was made by a"):
                self._check(["uvicorn", "config.asgi:application"])

    def test_migrate_and_test_are_not_checked(self):
        other = deployment.STANDALONE if not _STANDALONE_RUN else deployment.CONFIGURATOR
        with override_settings(AISC_DEPLOYMENT=other):
            for command in ("migrate", "test", "migrate_projects"):
                self._check(["manage.py", command])

    def test_a_server_of_the_same_mode_starts(self):
        self._check(["uvicorn", "config.asgi:application"])
