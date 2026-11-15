"""The engine's tables belong to engine_owner; a run's card version is fixed when it starts.

In a project database the engine migrates as engine_migrator (DB_MIGRATE_USER), whose sessions act as
engine_owner, a role no application logs in as. The engine serves as engine_rw, which from here on reads and
writes rows only: it cannot create, alter, truncate or put triggers on these tables, nor lift a guard.

An evaluation records the card version that is the latest when it starts (signals/system_stamp.py); from
then on that version is fixed: changing it, from none included, is refused by the trigger
`evaluation_system_is_fixed`, and the version cannot be deleted from under the run (the reference
restricts). The platform may read which version and which pid a run has, nothing else of it.

It stops first unless the schema is engine_owner's and the session acts as engine_owner: the platform hands
the schema over (template 0033) once postgres-setup has made the role. Safe on an install whose tables
engine_rw made: they are handed to the owner here. On sqlite, or on Postgres outside a project database
(the standalone engine), this does nothing.
"""
from django.db import migrations

STOP_UNLESS_OWNER = """
DO $owner$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_owner')
     OR (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'engine')
        IS DISTINCT FROM 'engine_owner'
     OR current_user <> 'engine_owner' THEN
    RAISE EXCEPTION 'engine_owner is missing or does not own schema engine: run postgres-setup and the platform first, then migrate as engine_migrator (DB_MIGRATE_USER)';
  END IF;
END
$owner$;
"""

FIXED_VERSION = """
ALTER TABLE engine.aisc_backend_evaluation DROP CONSTRAINT IF EXISTS aisc_backend_evaluation_system_id_fkey;
ALTER TABLE engine.aisc_backend_evaluation ADD CONSTRAINT aisc_backend_evaluation_system_id_fkey
  FOREIGN KEY (system_id) REFERENCES project.system (pid) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION engine.evaluation_system_is_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.system_id IS DISTINCT FROM NEW.system_id THEN
    RAISE EXCEPTION 'the card version of evaluation % is fixed at its start', OLD.id;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER evaluation_system_is_fixed BEFORE UPDATE OF system_id ON engine.aisc_backend_evaluation
  FOR EACH ROW EXECUTE FUNCTION engine.evaluation_system_is_fixed();
"""

HAND_OVER = """
REASSIGN OWNED BY engine_rw TO engine_owner;
REVOKE CREATE ON SCHEMA engine FROM engine_rw;
GRANT USAGE ON SCHEMA engine TO engine_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA engine FROM engine_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA engine TO engine_rw;
-- the migrations' own record is the migrator's: the engine may read it, not write it
REVOKE INSERT, UPDATE, DELETE ON engine.django_migrations FROM engine_rw;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA engine FROM engine_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA engine TO engine_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE engine_owner IN SCHEMA engine
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO engine_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE engine_owner IN SCHEMA engine
  GRANT USAGE, SELECT ON SEQUENCES TO engine_rw;
DO $platform$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_rw') THEN
    GRANT SELECT (system_id, pid) ON engine.aisc_backend_evaluation TO platform_rw;
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
        for sql in (STOP_UNLESS_OWNER, FIXED_VERSION, HAND_OVER):
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0027_component_retired_at"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
