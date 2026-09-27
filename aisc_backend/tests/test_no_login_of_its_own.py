"""The engine has no login of its own.

People sign in once, at the gateway (oauth2-proxy in front of Keycloak), and the
engine only reads who they are from the token the gateway passes on. Django's
accounts, sessions and admin site, allauth and ninja_jwt were a second way in
that nothing used: their tables were empty.
"""
from django.conf import settings
from django.db import connection
from django.test import TestCase

OTHER_LOGINS = (
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "allauth",
    "allauth.account",
    "allauth.headless",
    "ninja_jwt",
)

THEIR_TABLES = (
    "account_emailaddress",
    "account_emailconfirmation",
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "auth_user",
    "auth_user_groups",
    "auth_user_user_permissions",
    "django_admin_log",
    "django_session",
)


class NoLoginOfItsOwn(TestCase):
    def test_no_other_login_is_installed(self):
        for app in OTHER_LOGINS:
            self.assertNotIn(app, settings.INSTALLED_APPS, app)

    def test_no_middleware_keeps_a_session_of_its_own(self):
        for middleware in settings.MIDDLEWARE:
            self.assertNotIn("sessions", middleware)
            self.assertNotIn("contrib.auth", middleware)
            self.assertNotIn("allauth", middleware)

    def test_the_admin_site_is_gone(self):
        self.assertEqual(self.client.get("/admin/").status_code, 404)

    def test_the_allauth_endpoints_are_gone(self):
        self.assertEqual(self.client.get("/_allauth/browser/v1/config").status_code, 404)

    def test_their_tables_are_gone(self):
        tables = set(connection.introspection.table_names())
        for table in THEIR_TABLES:
            self.assertNotIn(table, tables, table)
