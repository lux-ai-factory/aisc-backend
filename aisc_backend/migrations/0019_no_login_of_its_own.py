"""The engine has no login of its own: drop what Django's accounts, sessions and
admin site, and allauth, left behind.

People sign in once, at the gateway, and the API reads who they are from its
token. These tables were empty. The apps that made them are no longer
installed, so Django would never touch them again; this removes them, and the
bookkeeping rows that still name those apps, so the schema holds the evaluation
and nothing else. Children before parents, and IF EXISTS throughout: a database
made after the apps went never had them.

Configurator only. A standalone engine keeps Django's accounts, sessions and
admin (Sean's login), so there this migration is recorded and does nothing. The
mode is read from settings, so a value set in .env counts.
"""
from django.conf import settings
from django.db import migrations

TABLES = (
    "account_emailconfirmation",
    "account_emailaddress",
    "django_admin_log",
    "auth_user_user_permissions",
    "auth_user_groups",
    "auth_group_permissions",
    "auth_user",
    "auth_group",
    "auth_permission",
    "django_session",
)
APPS = ("admin", "auth", "sessions", "account", "headless", "socialaccount", "ninja_jwt", "token_blacklist")


def forwards(apps, schema_editor):
    if settings.AISC_DEPLOYMENT != "configurator":
        return  # standalone keeps Django's accounts, sessions and admin (Sean's login)
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {quote(table)}")
        placeholders = ", ".join(["%s"] * len(APPS))
        cursor.execute(f"DELETE FROM django_migrations WHERE app IN ({placeholders})", APPS)
        cursor.execute(f"DELETE FROM django_content_type WHERE app_label IN ({placeholders})", APPS)


class Migration(migrations.Migration):
    dependencies = [
        ("aisc_backend", "0018_alter_project_platform_project_id"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
