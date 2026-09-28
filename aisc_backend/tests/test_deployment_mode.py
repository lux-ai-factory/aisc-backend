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

    def test_a_server_of_the_same_mode_starts(self):
        self._check(["uvicorn", "config.asgi:application"])


class TheCommandIsFound(SimpleTestCase):
    def test_the_first_argument_that_is_not_an_option(self):
        from aisc_backend.apps import command_of
        cases = {
            ("manage.py", "migrate"): "migrate",
            ("manage.py", "--settings=x", "migrate"): "migrate",
            ("manage.py", "--settings", "x", "migrate"): "migrate",
            ("manage.py", "--verbosity", "2", "test"): "test",
            ("manage.py", "-v", "2", "test"): "test",
            ("manage.py", "--pythonpath", "/p", "--no-color", "collectstatic"): "collectstatic",
            ("uvicorn", "config.asgi:application"): "config.asgi:application",
            ("manage.py",): None,
        }
        for argv, command in cases.items():
            with self.subTest(argv=argv):
                self.assertEqual(command_of(list(argv)), command)


class MigrateIsCheckedToo(TestCase):
    """Fix round 1: a migrate of the other mode would remake (or drop) the login tables."""

    def _other(self):
        return deployment.CONFIGURATOR if _STANDALONE_RUN else deployment.STANDALONE

    def _check(self, argv):
        from unittest import mock

        from aisc_backend.apps import AiscBackendConfig
        with mock.patch.object(sys, "argv", argv), mock.patch.object(connection, "close"):
            AiscBackendConfig._check_the_database_mode()

    def test_a_migrate_of_the_other_mode_is_refused_at_start(self):
        with override_settings(AISC_DEPLOYMENT=self._other()):
            for argv in (["manage.py", "migrate"], ["manage.py", "--settings=config.settings", "migrate"]):
                with self.subTest(argv=argv), self.assertRaisesRegex(ImproperlyConfigured, "made by a"):
                    self._check(argv)

    def test_a_migrate_of_the_other_mode_is_refused_by_migrate_itself(self):
        from django.core.management import call_command
        with override_settings(AISC_DEPLOYMENT=self._other()):
            with self.assertRaisesRegex(ImproperlyConfigured, "made by a"):
                call_command("migrate", verbosity=0, interactive=False)

    def test_a_fresh_database_is_not_blocked_under_migrate(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM engine_deployment")
        with override_settings(AISC_DEPLOYMENT=self._other()):
            self._check(["manage.py", "migrate"])

    def test_test_makemigrations_collectstatic_are_not_checked(self):
        with override_settings(AISC_DEPLOYMENT=self._other()):
            for argv in (["manage.py", "test"], ["manage.py", "-v", "2", "test"],
                         ["manage.py", "--verbosity", "2", "makemigrations"], ["manage.py", "collectstatic"]):
                with self.subTest(argv=argv):
                    self._check(argv)


class _FakeConnection:
    """What stamp/inference read: the table names, and the aisc_backend rows of django_migrations."""

    def __init__(self, tables, recorded, alias="default"):
        from unittest import mock
        self.alias = alias
        self.introspection = mock.Mock(table_names=mock.Mock(return_value=list(tables)))
        self.ops = mock.Mock(quote_name=lambda n: f'"{n}"')
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (recorded,)
        self.cursor = mock.MagicMock(return_value=mock.MagicMock(__enter__=mock.Mock(return_value=cursor)))


class Migration0025InfersAnOldDatabasesMode(SimpleTestCase):
    def test_a_pre_0021_standalone_database_keeps_its_login_tables(self):
        conn = _FakeConnection(["django_migrations", "aisc_backend_project", "auth_user"], recorded=20)
        self.assertEqual(deployment.infer_made_in(conn), deployment.STANDALONE)

    def test_a_pre_0021_configurator_database_lost_them_in_0019(self):
        conn = _FakeConnection(["django_migrations", "aisc_backend_project"], recorded=20)
        self.assertEqual(deployment.infer_made_in(conn), deployment.CONFIGURATOR)

    def test_a_fresh_database_infers_nothing(self):
        self.assertIsNone(deployment.infer_made_in(_FakeConnection([], recorded=0)))
        self.assertIsNone(deployment.infer_made_in(_FakeConnection(["django_migrations"], recorded=0)))

    def test_0021_stamps_what_migrate_found_before_it_ran_else_the_running_mode(self):
        for found, running, expected in ((deployment.STANDALONE, "configurator", "standalone"),
                                         (deployment.CONFIGURATOR, "standalone", "configurator"),
                                         (None, "configurator", "configurator"),
                                         (None, "standalone", "standalone")):
            with self.subTest(found=found, running=running), override_settings(AISC_DEPLOYMENT=running):
                deployment._found_before_migrate.clear()
                if found:
                    deployment._found_before_migrate["x"] = found
                self.assertEqual(deployment.stamp_for(_FakeConnection([], 0, alias="x")), expected)
        deployment._found_before_migrate.clear()

    def test_migrate_refuses_a_pre_0021_database_of_the_other_mode(self):
        from unittest import mock
        conn = _FakeConnection(["django_migrations", "aisc_backend_project"], recorded=20, alias="old")
        sender = mock.Mock()
        sender.name = "aisc_backend"
        with override_settings(AISC_DEPLOYMENT="standalone"), \
                mock.patch("django.db.connections", {"old": conn}):
            with self.assertRaisesRegex(ImproperlyConfigured, "made by a configurator engine"):
                deployment.before_migrate(sender=sender, using="old")
        deployment._found_before_migrate.clear()
