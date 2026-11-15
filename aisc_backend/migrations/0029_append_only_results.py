"""Measures, observations, the ledger outbox and saved plugin configurations are append-only.

In a project database, as engine_owner (the triggers hold for the owner too, and for the superuser):

- A measure or an observation is never changed, removed or truncated (`engine.refuse_change`). The engine
  reads and inserts them only.
- An outbox event is written through `engine.ledger_outbox_append` and marked delivered or refused through
  `engine.ledger_outbox_mark`, once: a mark moves from empty to set and never again. The event itself changes
  only by masking a secret in it (`engine.ledger_outbox_mask`, used by `manage.py clean_old_secrets`): a value
  replaced by "set" or a string by one with "[redacted]" in it, no member dropped. Rows are never removed. The engine reads the
  outbox and calls the three functions; it writes nothing else there.
- A saved plugin configuration is never removed, and its value changes only by masking: a field dropped or a
  string redacted (`engine.plugin_config_masked`), as `clean_old_secrets` does. Its name and description may
  change; which plugin it belongs to, its pid and when it was made may not.

Safe on an existing install: nothing is changed or removed; triggers, functions and grants only. On sqlite, or
on Postgres outside a project database (the standalone engine), this does nothing.
"""
from django.db import migrations

APPEND_ONLY = """
CREATE OR REPLACE FUNCTION engine.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on %: its rows are append-only, never changed or removed', TG_OP, TG_TABLE_NAME;
END $$;

CREATE OR REPLACE TRIGGER aisc_backend_measurement_append_only BEFORE UPDATE OR DELETE
  ON engine.aisc_backend_measurement FOR EACH ROW EXECUTE FUNCTION engine.refuse_change();
CREATE OR REPLACE TRIGGER aisc_backend_measurement_no_truncate BEFORE TRUNCATE
  ON engine.aisc_backend_measurement FOR EACH STATEMENT EXECUTE FUNCTION engine.refuse_change();
CREATE OR REPLACE TRIGGER aisc_backend_observation_append_only BEFORE UPDATE OR DELETE
  ON engine.aisc_backend_observation FOR EACH ROW EXECUTE FUNCTION engine.refuse_change();
CREATE OR REPLACE TRIGGER aisc_backend_observation_no_truncate BEFORE TRUNCATE
  ON engine.aisc_backend_observation FOR EACH STATEMENT EXECUTE FUNCTION engine.refuse_change();

REVOKE UPDATE, DELETE ON engine.aisc_backend_measurement, engine.aisc_backend_observation FROM engine_rw;
"""

MASKED = """
-- `new` is `old` with secrets masked: a string replaced by one holding [redacted], object members dropped when
-- `may_drop`, and any value replaced by `placeholder` when one is given. Recursive; arrays keep their length.
CREATE OR REPLACE FUNCTION engine.json_masked(old jsonb, new jsonb, may_drop boolean, placeholder text)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  member record;
  i integer;
BEGIN
  IF old IS NOT DISTINCT FROM new THEN
    RETURN true;
  END IF;
  IF old IS NULL OR new IS NULL THEN
    RETURN false;
  END IF;
  IF placeholder IS NOT NULL AND new = to_jsonb(placeholder) THEN
    RETURN true;
  END IF;
  IF jsonb_typeof(old) = 'object' AND jsonb_typeof(new) = 'object' THEN
    IF NOT may_drop AND EXISTS (SELECT 1 FROM jsonb_object_keys(old) k WHERE NOT new ? k) THEN
      RETURN false;
    END IF;
    FOR member IN SELECT key, value FROM jsonb_each(new) LOOP
      IF NOT old ? member.key OR NOT engine.json_masked(old -> member.key, member.value, may_drop, placeholder) THEN
        RETURN false;
      END IF;
    END LOOP;
    RETURN true;
  END IF;
  IF jsonb_typeof(old) = 'array' AND jsonb_typeof(new) = 'array' THEN
    IF jsonb_array_length(old) <> jsonb_array_length(new) THEN
      RETURN false;
    END IF;
    FOR i IN 0 .. jsonb_array_length(new) - 1 LOOP
      IF NOT engine.json_masked(old -> i, new -> i, may_drop, placeholder) THEN
        RETURN false;
      END IF;
    END LOOP;
    RETURN true;
  END IF;
  IF jsonb_typeof(old) = 'string' AND jsonb_typeof(new) = 'string' THEN
    RETURN position('[redacted]' IN new #>> '{}') > 0;
  END IF;
  RETURN false;
END $$;

CREATE OR REPLACE FUNCTION engine.plugin_config_masked(old jsonb, new jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$ SELECT engine.json_masked(old, new, true, NULL) $$;
"""

OUTBOX = """
CREATE OR REPLACE FUNCTION engine.ledger_outbox_only_marks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'ledger outbox event % is kept: it is never removed', OLD.id;
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.project_pid IS DISTINCT FROM OLD.project_pid
     OR NEW.queued_at IS DISTINCT FROM OLD.queued_at THEN
    RAISE EXCEPTION 'ledger outbox event % is fixed: only its delivery mark may be set', OLD.id;
  END IF;
  IF NOT engine.json_masked(OLD.event, NEW.event, false, 'set') THEN
    RAISE EXCEPTION 'ledger outbox event % is fixed: it changes only by masking a secret in it', OLD.id;
  END IF;
  IF (OLD.delivered_at IS NOT NULL OR OLD.refused IS NOT NULL)
     AND (NEW.delivered_at IS DISTINCT FROM OLD.delivered_at OR NEW.refused IS DISTINCT FROM OLD.refused) THEN
    RAISE EXCEPTION 'ledger outbox event % is marked already: a mark is set once', OLD.id;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER ledgeroutbox_only_marks BEFORE UPDATE
  ON engine.aisc_backend_ledgeroutbox FOR EACH ROW EXECUTE FUNCTION engine.ledger_outbox_only_marks();
CREATE OR REPLACE TRIGGER ledgeroutbox_no_delete BEFORE DELETE
  ON engine.aisc_backend_ledgeroutbox FOR EACH ROW EXECUTE FUNCTION engine.ledger_outbox_only_marks();
CREATE OR REPLACE TRIGGER ledgeroutbox_no_truncate BEFORE TRUNCATE
  ON engine.aisc_backend_ledgeroutbox FOR EACH STATEMENT EXECUTE FUNCTION engine.refuse_change();

CREATE OR REPLACE FUNCTION engine.ledger_outbox_append(project_pid uuid, event jsonb) RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
  INSERT INTO engine.aisc_backend_ledgeroutbox (project_pid, event, queued_at)
  VALUES ($1, $2, clock_timestamp()) RETURNING id
$$;

-- Marks the event delivered (now) or refused (with the platform's status), only while it has no mark;
-- whether it did.
CREATE OR REPLACE FUNCTION engine.ledger_outbox_mark(id bigint, delivered boolean, refused integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
DECLARE
  changed integer;
BEGIN
  UPDATE engine.aisc_backend_ledgeroutbox o
     SET delivered_at = CASE WHEN delivered THEN clock_timestamp() END,
         refused = CASE WHEN delivered THEN NULL ELSE ledger_outbox_mark.refused END
   WHERE o.id = ledger_outbox_mark.id AND o.delivered_at IS NULL AND o.refused IS NULL;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed > 0;
END $$;

-- The event with a secret masked in it (the trigger refuses anything else); whether the row exists.
CREATE OR REPLACE FUNCTION engine.ledger_outbox_mask(id bigint, event jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, engine, pg_temp AS $$
DECLARE
  changed integer;
BEGIN
  UPDATE engine.aisc_backend_ledgeroutbox o SET event = ledger_outbox_mask.event
   WHERE o.id = ledger_outbox_mask.id;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed > 0;
END $$;

REVOKE ALL ON FUNCTION engine.ledger_outbox_append(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.ledger_outbox_mark(bigint, boolean, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.ledger_outbox_mask(bigint, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.ledger_outbox_append(uuid, jsonb) TO engine_rw;
GRANT EXECUTE ON FUNCTION engine.ledger_outbox_mark(bigint, boolean, integer) TO engine_rw;
GRANT EXECUTE ON FUNCTION engine.ledger_outbox_mask(bigint, jsonb) TO engine_rw;

REVOKE INSERT, UPDATE, DELETE ON engine.aisc_backend_ledgeroutbox FROM engine_rw;
GRANT SELECT ON engine.aisc_backend_ledgeroutbox TO engine_rw;
"""

PLUGIN_CONFIG = """
CREATE OR REPLACE FUNCTION engine.pluginconfig_is_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'saved configuration % is kept: it is never removed', OLD.id;
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.pid IS DISTINCT FROM OLD.pid
     OR NEW.plugin_id IS DISTINCT FROM OLD.plugin_id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'saved configuration % is fixed: only its name and description may change', OLD.id;
  END IF;
  IF NOT engine.plugin_config_masked(OLD.config, NEW.config) THEN
    RAISE EXCEPTION 'saved configuration % is fixed: its value changes only by masking a secret in it', OLD.id;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER pluginconfig_is_fixed BEFORE UPDATE OR DELETE
  ON engine.aisc_backend_pluginconfig FOR EACH ROW EXECUTE FUNCTION engine.pluginconfig_is_fixed();
CREATE OR REPLACE TRIGGER pluginconfig_no_truncate BEFORE TRUNCATE
  ON engine.aisc_backend_pluginconfig FOR EACH STATEMENT EXECUTE FUNCTION engine.refuse_change();

REVOKE DELETE ON engine.aisc_backend_pluginconfig FROM engine_rw;
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
        for sql in (APPEND_ONLY, MASKED, OUTBOX, PLUGIN_CONFIG):
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0028_owner_role"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
