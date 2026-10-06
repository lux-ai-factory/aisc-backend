"""One system target per system (code review 2026-10-06).

The evaluation form's GET made sure of the system target (routers/target_input.py) by checking, then
creating: two forms loaded at once could make two. This merges duplicates, keeping the oldest and moving
to it what referred to the others (evaluation inputs, derived components), then adds a unique index on
(system, the component's value) for system targets (`target:<platform pid>/system`).

An expression index in SQL rather than a model constraint: AIComponent is Sean's model, and a constraint
in its Meta would change his file; the index is invisible to Django's model state, so makemigrations
stays empty.
"""
from django.db import migrations

INDEX = "aisc_one_system_target_per_system"
SYSTEM_TARGET = "target:%/system"

_CREATE = {
    "postgresql": (f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON aisc_backend_aicomponent "
                   f"(system_id, (json_value->>'value')) WHERE (json_value->>'value') LIKE '{SYSTEM_TARGET}'"),
    "sqlite": (f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON aisc_backend_aicomponent "
               f"(system_id, json_extract(json_value, '$.value')) "
               f"WHERE json_extract(json_value, '$.value') LIKE '{SYSTEM_TARGET}'"),
}


def merge_duplicates(apps, schema_editor):
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    EvaluationInput = apps.get_model("aisc_backend", "EvaluationInput")
    seen = {}
    for component in AIComponent.objects.order_by("pk"):
        value = (component.json_value or {}).get("value") if isinstance(component.json_value, dict) else None
        if not (isinstance(value, str) and value.startswith("target:") and value.endswith("/system")):
            continue
        key = (component.system_id, value)
        if key not in seen:
            seen[key] = component.pk
            continue
        keep = seen[key]
        EvaluationInput.objects.filter(component_id=component.pk).update(component_id=keep)
        AIComponent.objects.filter(source_dataset_id=component.pk).update(source_dataset_id=keep)
        component.delete()


def create_index(apps, schema_editor):
    sql = _CREATE.get(schema_editor.connection.vendor)
    if sql:
        # no parameters: with any (even none, ()) psycopg reads the LIKE pattern's % as a placeholder
        schema_editor.execute(sql, None)


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor in _CREATE:
        schema_editor.execute(f"DROP INDEX IF EXISTS {INDEX}", None)


def forwards(apps, schema_editor):
    merge_duplicates(apps, schema_editor)
    create_index(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [("aisc_backend", "0021_engine_deployment_marker")]

    operations = [migrations.RunPython(forwards, drop_index)]
