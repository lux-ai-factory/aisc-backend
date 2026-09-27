"""The database remembers the mode it was made in (engine deployment modes, 2026-09-27).

A table `engine_deployment` with one row, the AISC_DEPLOYMENT of the engine that
ran this migration. The schemas of the two modes differ (0023 drops the login
tables in configurator only), so aisc_backend.deployment.assert_database_mode
refuses to run an engine of the other mode on it.

A database that already existed is stamped with what it was, not with the mode
that happens to migrate it: `configurator` when 0023 has dropped `auth_user`,
`standalone` when it is there. That is read by deployment.before_migrate
(pre_migrate) before any migration of the run, so the login tables are as the
database had them. A fresh database is stamped with the running mode.
"""
from django.db import migrations

from aisc_backend import deployment

TABLE = "engine_deployment"


def forwards(apps, schema_editor):
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {quote(TABLE)} ({quote('mode')} varchar(16) NOT NULL)")
        cursor.execute(f"INSERT INTO {quote(TABLE)} ({quote('mode')}) VALUES (%s)",
                       [deployment.stamp_for(schema_editor.connection)])


def backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {schema_editor.quote_name(TABLE)}")


class Migration(migrations.Migration):
    dependencies = [
        ("aisc_backend", "0024_the_database_is_the_project"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
