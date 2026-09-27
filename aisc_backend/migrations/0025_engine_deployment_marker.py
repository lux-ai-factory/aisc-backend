"""The database remembers the mode it was made in (engine deployment modes, 2026-09-27).

A table `engine_deployment` with one row, the AISC_DEPLOYMENT of the engine that
ran this migration. The schemas of the two modes differ (0023 drops the login
tables in configurator only), so aisc_backend.deployment.assert_database_mode
refuses to run an engine of the other mode on it.
"""
from django.conf import settings
from django.db import migrations

TABLE = "engine_deployment"


def forwards(apps, schema_editor):
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {quote(TABLE)} ({quote('mode')} varchar(16) NOT NULL)")
        cursor.execute(f"INSERT INTO {quote(TABLE)} ({quote('mode')}) VALUES (%s)", [settings.AISC_DEPLOYMENT])


def backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {schema_editor.quote_name(TABLE)}")


class Migration(migrations.Migration):
    dependencies = [
        ("aisc_backend", "0024_the_database_is_the_project"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
