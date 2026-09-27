"""The parts of a project's AI system belong to a version of it.

The engine kept its own AISystem row per project (0020), beside the platform's
system, and the two named the same thing twice. Now the system is the
platform's one: core.ai_system, in versions (core.ai_system_version). A part
belongs to the engine project and to one of those versions; the engine's
AISystem table goes.

Existing parts join the version the platform carried their project's system
over to (platform migration 0002 keeps core.system's pids), and the engine
records that it has that version's parts. On sqlite, and for a project the
platform does not know, parts stay unversioned.

The keys into core are made in SQL, only on Postgres and only when core has
the table, as in 0015.
"""
import django.db.models.deletion
from django.db import migrations, models

LINK_TO_CORE = """
DO $$
BEGIN
    IF to_regclass('core.ai_system_version') IS NOT NULL THEN
        ALTER TABLE ai_component
            ADD CONSTRAINT ai_component_system_version_id_fkey
            FOREIGN KEY (system_version_id) REFERENCES core.ai_system_version (pid)
            -- as for the project link (0015): a project deleted on the platform
            -- takes its versions with it, and must not be refused for it
            ON DELETE SET NULL;
        ALTER TABLE evaluation
            DROP CONSTRAINT IF EXISTS aisc_backend_evaluation_system_id_fkey;
        ALTER TABLE evaluation
            ADD CONSTRAINT evaluation_system_version_id_fkey
            FOREIGN KEY (system_id) REFERENCES core.ai_system_version (pid)
            ON DELETE SET NULL;
    END IF;
END $$;
"""

UNLINK = """
ALTER TABLE ai_component DROP CONSTRAINT IF EXISTS ai_component_system_version_id_fkey;
ALTER TABLE evaluation DROP CONSTRAINT IF EXISTS evaluation_system_version_id_fkey;
"""

CURRENT_VERSION = """
SELECT v.pid, v.number
  FROM core.ai_system a
  JOIN core.ai_system_version v ON v.ai_system_id = a.pid
 WHERE a.project_id = %s
 ORDER BY v.number DESC
 LIMIT 1
"""


def parts_join_their_version(apps, schema_editor):
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    SystemVersionParts = apps.get_model("aisc_backend", "SystemVersionParts")
    connection = schema_editor.connection
    has_core = False
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('core.ai_system_version') IS NOT NULL")
            has_core = cursor.fetchone()[0]

    versions = {}
    for part in AIComponent.objects.select_related("system__project"):
        project = part.system.project
        part.project = project
        part.lineage = part.pid
        platform_project = project.platform_project_id
        if has_core and platform_project is not None:
            if platform_project not in versions:
                with connection.cursor() as cursor:
                    cursor.execute(CURRENT_VERSION, [str(platform_project)])
                    versions[platform_project] = cursor.fetchone()
            found = versions[platform_project]
            if found is not None:
                part.system_version_id = found[0]
                SystemVersionParts.objects.get_or_create(
                    version_pid=found[0], defaults={"number": found[1], "project": project})
        part.save(update_fields=["project", "lineage", "system_version_id"])


def link_to_core(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(LINK_TO_CORE)


def unlink_from_core(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(UNLINK)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0021_ai_system_tables_lose_the_prefix"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemVersionParts",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_pid", models.UUIDField(unique=True)),
                ("number", models.PositiveIntegerField()),
                ("copied_from", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="system_versions", to="aisc_backend.project")),
            ],
            options={"db_table": "system_version_parts"},
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="project",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="components", to="aisc_backend.project"),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="system_version_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="lineage",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(parts_join_their_version, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aicomponent",
            name="project",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name="components", to="aisc_backend.project"),
        ),
        migrations.RemoveField(model_name="aicomponent", name="system"),
        migrations.DeleteModel(name="AISystem"),
        migrations.RunPython(link_to_core, unlink_from_core),
    ]
