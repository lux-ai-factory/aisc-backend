"""Conversation text already stored in measures can be replaced by its trace reference, and only that.

`engine.measurement_dimensions_referenced(measurement id, dimensions)` sets a measure's dimensions when the new
ones are the old ones with conversation values (prompt, response, conversation, answer, reply, request; any
case) replaced by `trace:<test pid>#sha256=<digest of the old text>`: the same keys, every other value the same.
Anything else is refused. It is what `manage.py reference_conversation_dimensions apply` uses, a one-off run
only with the owner's yes; the engine's runtime role may call it, as it calls the outbox's masking function.

The append-only guard lets an UPDATE of a measure through only inside that function. Safe on an existing
install: functions and a grant, no row changes. On sqlite, or on Postgres outside a project database (the
standalone engine), this does nothing.
"""
from django.db import migrations

GUARD = """
CREATE OR REPLACE FUNCTION engine.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND TG_TABLE_NAME IN ('aisc_backend_measurement', 'aisc_backend_observation')
     AND current_user = 'engine_owner' AND current_setting('aisc.purging', true) = 'evaluation' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'aisc_backend_measurement'
     AND current_user = 'engine_owner' AND current_setting('aisc.purging', true) = 'dimensions' THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% on %: its rows are append-only, never changed or removed', TG_OP, TG_TABLE_NAME;
END $$;
"""

REFERENCED = """
CREATE OR REPLACE FUNCTION engine.measurement_dimensions_referenced(p_id bigint, p_dimensions jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
DECLARE
  old jsonb;
  test uuid;
  member record;
BEGIN
  SELECT m.dimensions, t.pid INTO old, test
    FROM engine.aisc_backend_measurement m
    JOIN engine.aisc_backend_observation o ON o.id = m.observation_id
    JOIN engine.aisc_backend_evaluationplugin t ON t.id = o.evaluation_plugin_id
   WHERE m.id = p_id;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  IF old IS NULL OR jsonb_typeof(old) <> 'object' OR p_dimensions IS NULL OR jsonb_typeof(p_dimensions) <> 'object'
     OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(old) k)
        IS DISTINCT FROM (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_dimensions) k) THEN
    RAISE EXCEPTION 'measure %: its dimensions keep their keys', p_id;
  END IF;
  FOR member IN SELECT key, value FROM jsonb_each(old) LOOP
    CONTINUE WHEN p_dimensions -> member.key = member.value;
    IF lower(member.key) NOT IN ('prompt', 'response', 'conversation', 'answer', 'reply', 'request')
       OR jsonb_typeof(member.value) <> 'string'
       OR p_dimensions ->> member.key IS DISTINCT FROM
          'trace:' || test::text || '#sha256=' || encode(sha256(convert_to(member.value #>> '{}', 'UTF8')), 'hex') THEN
      RAISE EXCEPTION 'measure %: only conversation text is replaced, by the reference of that text', p_id;
    END IF;
  END LOOP;
  PERFORM set_config('aisc.purging', 'dimensions', true);
  UPDATE engine.aisc_backend_measurement SET dimensions = p_dimensions WHERE id = p_id;
  PERFORM set_config('aisc.purging', '', true);
  RETURN true;
END $$;

REVOKE ALL ON FUNCTION engine.measurement_dimensions_referenced(bigint, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.measurement_dimensions_referenced(bigint, jsonb) TO engine_rw;
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
        for sql in (GUARD, REFERENCED):
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0039_artifact_holds_conversation"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
