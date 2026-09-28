import unittest

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from aisc_backend import deployment

_CONFIGURATOR_RUN = settings.AISC_DEPLOYMENT == deployment.CONFIGURATOR


@unittest.skipUnless(_CONFIGURATOR_RUN, "configurator run only")
class TheConfiguratorStack(SimpleTestCase):
    def test_door_and_no_login_of_its_own(self):
        self.assertEqual(settings.AISC_DEPLOYMENT, "configurator")
        self.assertIn("aisc_backend.project_door.ProjectDoor", settings.MIDDLEWARE)
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertFalse([a for a in settings.INSTALLED_APPS if a.startswith(("allauth", "ninja_jwt"))])
        self.assertFalse([m for m in settings.MIDDLEWARE if "allauth" in m])

    def test_no_login_urls(self):
        from django.urls import Resolver404, resolve
        for path in ("/admin/", "/_allauth/browser/v1/auth/session"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    @unittest.skipUnless(settings.PROJECT_DATABASES, "project databases (configurator on Postgres) only")
    def test_one_database_per_project_behind_the_router(self):
        self.assertEqual(settings.DATABASE_ROUTERS, ["aisc_backend.projectdb.ProjectDatabaseRouter"])
        self.assertTrue(settings.PROJECT_DATABASES)
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.dummy")
        self.assertIn("platform", settings.DATABASES)
        self.assertEqual(settings.PROJECT_DATABASE_TEMPLATE["ENGINE"], "django.db.backends.postgresql")


@unittest.skipUnless(_CONFIGURATOR_RUN and not settings.PROJECT_DATABASES,
                     "configurator on one database (sqlite)")
class TheLoginTablesStayGone(TransactionTestCase):
    """Ruling 9: 0019 drops the login tables, and no installed app makes them again."""

    LOGIN_TABLES = ("auth_user", "auth_permission", "django_session", "django_admin_log", "account_emailaddress")

    def _tables(self):
        return set(connection.introspection.table_names())

    def test_a_second_migrate_does_not_remake_them(self):
        self.assertFalse(self._tables() & set(self.LOGIN_TABLES))
        call_command("migrate", verbosity=0, interactive=False)
        self.assertFalse(self._tables() & set(self.LOGIN_TABLES))
