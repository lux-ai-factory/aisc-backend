"""A run's results leave a project database only through the owner-only purge.

`engine.purge_evaluation(evaluation pid)` removes the run's measures, observations and artifact rows, marks the
run purged (`purged_at`) and keeps its row. It answers what the platform must do next:
`{"artifacts": [stored object names], "measures": n, "evaluation_plugins": [test pids]}`, or NULL when the
project has no such run. It is SECURITY DEFINER, owned by engine_owner, and only platform_rw may call it: the
platform's owner-only route decides, the engine never does.

The append-only guard on measures and observations lets a DELETE through only inside that function: the
session acts as engine_owner and the function has marked the transaction as purging. Any other DELETE is
refused as before, the engine's runtime role and engine_owner's own migrations included.

Safe on an existing install: one empty column, a function and grants. On sqlite, or on Postgres outside a
project database (the standalone engine), only the column is added.
"""
from django.db import migrations, models

GUARD_WITH_PURGE = """
CREATE OR REPLACE FUNCTION engine.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND TG_TABLE_NAME IN ('aisc_backend_measurement', 'aisc_backend_observation')
     AND current_user = 'engine_owner' AND current_setting('aisc.purging', true) = 'evaluation' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '% on %: its rows are append-only, never changed or removed', TG_OP, TG_TABLE_NAME;
END $$;
"""

PURGE = """
CREATE OR REPLACE FUNCTION engine.purge_evaluation(p_evaluation uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
DECLARE
  the_run bigint;
  objects jsonb;
  tests jsonb;
  measures integer;
BEGIN
  SELECT e.id INTO the_run FROM engine.aisc_backend_evaluation e WHERE e.pid = p_evaluation FOR UPDATE;
  IF the_run IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT coalesce(jsonb_agg(a.data ORDER BY a.id), '[]'::jsonb) INTO objects
    FROM engine.aisc_backend_artifact a
    JOIN engine.aisc_backend_evaluationplugin t ON t.id = a.evaluation_plugin_id
   WHERE t.evaluation_id = the_run AND coalesce(a.data, '') <> '';
  SELECT coalesce(jsonb_agg(t.pid::text ORDER BY t.id), '[]'::jsonb) INTO tests
    FROM engine.aisc_backend_evaluationplugin t WHERE t.evaluation_id = the_run;

  PERFORM set_config('aisc.purging', 'evaluation', true);
  DELETE FROM engine.aisc_backend_measurement m USING engine.aisc_backend_observation o
   WHERE o.id = m.observation_id AND o.evaluation_id = the_run;
  GET DIAGNOSTICS measures = ROW_COUNT;
  DELETE FROM engine.aisc_backend_observation o WHERE o.evaluation_id = the_run;
  DELETE FROM engine.aisc_backend_artifact a USING engine.aisc_backend_evaluationplugin t
   WHERE t.id = a.evaluation_plugin_id AND t.evaluation_id = the_run;
  PERFORM set_config('aisc.purging', '', true);

  UPDATE engine.aisc_backend_evaluation e SET purged_at = clock_timestamp() WHERE e.id = the_run;
  RETURN jsonb_build_object('artifacts', objects, 'measures', measures, 'evaluation_plugins', tests);
END $$;

REVOKE ALL ON FUNCTION engine.purge_evaluation(uuid) FROM PUBLIC;
DO $platform$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_rw') THEN
    GRANT USAGE ON SCHEMA engine TO platform_rw;
    GRANT EXECUTE ON FUNCTION engine.purge_evaluation(uuid) TO platform_rw;
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
        # without parameters: several statements in one, and the RAISE formats keep their %
        for sql in (GUARD_WITH_PURGE, PURGE):
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0035_run_code_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluation",
            name="purged_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
