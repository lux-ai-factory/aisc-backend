"""The platform deletes a project's files from the engine's shared buckets when it deletes the project.

The buckets (datasets, models, artifacts) are shared by every project and keyed by uuid, so only the project's
database says which files are its: `engine.stored_objects()` names each (bucket, name), every component file
and every run artifact. SECURITY DEFINER, owned by engine_owner; only platform_rw may call it, and it reads
nothing else of the engine.

Safe on an existing install: a function and a grant. On sqlite, or on Postgres outside a project database (the
standalone engine), this does nothing.
"""
from django.db import migrations

STORED_OBJECTS = """
CREATE OR REPLACE FUNCTION engine.stored_objects() RETURNS TABLE (bucket text, name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
  SELECT c.storage_container::text, c.data::text FROM engine.aisc_backend_aicomponent c
   WHERE coalesce(c.data, '') <> '' AND coalesce(c.storage_container, '') <> ''
  UNION
  SELECT a.storage_container::text, a.data::text FROM engine.aisc_backend_artifact a
   WHERE coalesce(a.data, '') <> '' AND coalesce(a.storage_container, '') <> ''
$$;

REVOKE ALL ON FUNCTION engine.stored_objects() FROM PUBLIC;
DO $platform$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_rw') THEN
    GRANT USAGE ON SCHEMA engine TO platform_rw;
    GRANT EXECUTE ON FUNCTION engine.stored_objects() TO platform_rw;
  END IF;
END
$platform$;
"""


def forwards(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database() ~ '^project_[0-9a-f]{32}$'"
                       " AND to_regclass('project.system') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return
        cursor.execute(STORED_OBJECTS)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0036_run_purge"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
