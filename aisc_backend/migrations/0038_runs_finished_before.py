"""The platform's retention sweep asks which runs ended before a moment.

`engine.runs_finished_before(moment)` answers the pid of every run that is done, failed or archived and not
purged yet, whose last test ended before the moment (or which was made before it, when no test says when it
ended). SECURITY DEFINER, owned by engine_owner; only platform_rw may call it, and it reads nothing else.

Safe on an existing install: a function and a grant. On sqlite, or on Postgres outside a project database (the
standalone engine), this does nothing.
"""
from django.db import migrations

RUNS_FINISHED_BEFORE = """
CREATE OR REPLACE FUNCTION engine.runs_finished_before(p_before timestamptz) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
  SELECT e.pid FROM engine.aisc_backend_evaluation e
   WHERE e.purged_at IS NULL AND e.status IN ('Done', 'Failed', 'Archived')
     AND coalesce((SELECT max(t.finished_at) FROM engine.aisc_backend_evaluationplugin t
                    WHERE t.evaluation_id = e.id), e.created_at) < p_before
   ORDER BY e.created_at, e.id
$$;

REVOKE ALL ON FUNCTION engine.runs_finished_before(timestamptz) FROM PUBLIC;
DO $platform$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_rw') THEN
    GRANT USAGE ON SCHEMA engine TO platform_rw;
    GRANT EXECUTE ON FUNCTION engine.runs_finished_before(timestamptz) TO platform_rw;
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
        cursor.execute(RUNS_FINISHED_BEFORE)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0037_stored_objects"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
