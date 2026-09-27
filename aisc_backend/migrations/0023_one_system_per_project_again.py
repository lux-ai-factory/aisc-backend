"""One AI system per project again, as at Sean's merge (e34fca3).

0022 moved the parts of a project's system onto a version of the platform's
system and dropped the engine's AISystem table. The decided model (pipeline
2026-09-23) keeps the engine data model exactly as it was at e34fca3: one
AISystem per engine project, its parts on it, and the card versions live on
the platform, in core.system. This migration undoes 0022:

- every part goes back onto its project's one AISystem (made here, named as
  e34fca3's get_or_create_aisystem names it); part pids are untouched;
- the version fields of the parts and the SystemVersionParts table go;
- the constraint, index and sequence names are restored to the e34fca3 dump,
  because Postgres keeps the old names across the 0021 table renames and a
  fresh CreateModel here would name them after the new table;
- the evaluation's key into core.system (0015) is restored where 0022 pointed
  it at core.ai_system_version.

Statements about core run only on Postgres and only when core has the table.
Every RunPython has a noop reverse, so the migration can be undone on empty
tables.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


def _pg(schema_editor):
    return schema_editor.connection.vendor == "postgresql"


def _run(schema_editor, sql):
    # A plain cursor with no parameters, so the % of format() and LIKE are
    # not taken for placeholders.
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql)


DROP_VERSION_LINKS = """
ALTER TABLE ai_component DROP CONSTRAINT IF EXISTS ai_component_system_version_id_fkey;
ALTER TABLE evaluation DROP CONSTRAINT IF EXISTS evaluation_system_version_id_fkey;
"""

RESTORE_REFERENCE_NAMES = """
DO $$
DECLARE n text;
BEGIN
  SELECT conname INTO n FROM pg_constraint WHERE conrelid = 'ai_system'::regclass AND contype = 'p';
  IF n IS NOT NULL AND n <> 'aisc_backend_aisystem_pkey' THEN
    EXECUTE format('ALTER TABLE ai_system RENAME CONSTRAINT %I TO aisc_backend_aisystem_pkey', n);
  END IF;

  SELECT conname INTO n FROM pg_constraint WHERE conrelid = 'ai_system'::regclass AND contype = 'u';
  IF n IS NOT NULL AND n <> 'aisc_backend_aisystem_project_id_key' THEN
    EXECUTE format('ALTER TABLE ai_system RENAME CONSTRAINT %I TO aisc_backend_aisystem_project_id_key', n);
  END IF;

  SELECT conname INTO n FROM pg_constraint WHERE conrelid = 'ai_system'::regclass AND contype = 'f';
  IF n IS NOT NULL AND n <> 'aisc_backend_aisystem_project_id_381e09a9_fk_project_id' THEN
    EXECUTE format('ALTER TABLE ai_system RENAME CONSTRAINT %I TO aisc_backend_aisystem_project_id_381e09a9_fk_project_id', n);
  END IF;

  SELECT conname INTO n FROM pg_constraint
   WHERE conrelid = 'ai_component'::regclass AND contype = 'f' AND confrelid = 'ai_system'::regclass;
  IF n IS NOT NULL AND n <> 'aisc_backend_aicompo_system_id_0909bda5_fk_aisc_back' THEN
    EXECUTE format('ALTER TABLE ai_component RENAME CONSTRAINT %I TO aisc_backend_aicompo_system_id_0909bda5_fk_aisc_back', n);
  END IF;

  SELECT i.relname INTO n FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
   WHERE x.indrelid = 'ai_component'::regclass AND NOT x.indisprimary
     AND x.indkey::text = (SELECT attnum::text FROM pg_attribute
                            WHERE attrelid = 'ai_component'::regclass AND attname = 'system_id');
  IF n IS NOT NULL AND n <> 'aisc_backend_aicomponent_system_id_0909bda5' THEN
    EXECUTE format('ALTER INDEX %I RENAME TO aisc_backend_aicomponent_system_id_0909bda5', n);
  END IF;

  n := pg_get_serial_sequence('ai_system', 'id');
  IF n IS NOT NULL AND n NOT LIKE '%aisc_backend_aisystem_id_seq' THEN
    EXECUTE format('ALTER SEQUENCE %s RENAME TO aisc_backend_aisystem_id_seq', n);
  END IF;
END $$;
"""

LINK_EVALUATION_TO_CORE_SYSTEM = """
DO $$
BEGIN
  IF to_regclass('core.system') IS NOT NULL THEN
    UPDATE evaluation SET system_id = NULL
     WHERE system_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM core.system s WHERE s.pid = evaluation.system_id);
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'evaluation'::regclass
                      AND conname = 'aisc_backend_evaluation_system_id_fkey') THEN
      ALTER TABLE evaluation ADD CONSTRAINT aisc_backend_evaluation_system_id_fkey
        FOREIGN KEY (system_id) REFERENCES core.system (pid) ON DELETE SET NULL;
    END IF;
  END IF;
END $$;
"""


def drop_version_links(apps, schema_editor):
    if _pg(schema_editor):
        _run(schema_editor, DROP_VERSION_LINKS)


def parts_back_on_the_system(apps, schema_editor):
    db = schema_editor.connection.alias
    Project = apps.get_model("aisc_backend", "Project")
    AISystem = apps.get_model("aisc_backend", "AISystem")
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    for project in Project.objects.using(db).all():
        system = AISystem.objects.using(db).create(
            project=project, name=f"{project.name} system", description="")
        AIComponent.objects.using(db).filter(project=project).update(system=system)
    if _pg(schema_editor):
        # The keys are deferred: check them now, so the ALTER TABLEs that follow
        # in this transaction do not meet pending trigger events.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        schema_editor.execute("SET CONSTRAINTS ALL DEFERRED")


def restore_reference_names(apps, schema_editor):
    if _pg(schema_editor):
        # Django keeps the keys and indexes of the new AISystem and of
        # AIComponent.system for the end of the migration; make them now, so
        # that they can be renamed here.
        while schema_editor.deferred_sql:
            schema_editor.execute(schema_editor.deferred_sql.pop(0))
        _run(schema_editor, RESTORE_REFERENCE_NAMES)


def link_evaluation_to_core_system(apps, schema_editor):
    if _pg(schema_editor):
        _run(schema_editor, LINK_EVALUATION_TO_CORE_SYSTEM)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0022_parts_belong_to_a_version_of_the_one_system"),
    ]

    operations = [
        migrations.RunPython(drop_version_links, migrations.RunPython.noop),
        migrations.CreateModel(
            name="AISystem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pid", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("project", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                 related_name="aisystem", to="aisc_backend.project")),
            ],
            options={"db_table": "ai_system"},
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="system",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="components", to="aisc_backend.aisystem"),
        ),
        migrations.RunPython(parts_back_on_the_system, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aicomponent",
            name="system",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name="components", to="aisc_backend.aisystem"),
        ),
        migrations.RemoveField(model_name="aicomponent", name="project"),
        migrations.RemoveField(model_name="aicomponent", name="system_version_id"),
        migrations.RemoveField(model_name="aicomponent", name="lineage"),
        migrations.DeleteModel(name="SystemVersionParts"),
        migrations.RunPython(restore_reference_names, migrations.RunPython.noop),
        migrations.RunPython(link_evaluation_to_core_system, migrations.RunPython.noop),
    ]
