"""Dissolve CON-01's Connection, Helper and RunBinding into endpoint components
(ADR 0009).

Only unshipped unified-app data is touched, and a best-effort conversion is enough
(ADR 0003): a dev database keeps its setup, and no preservation guarantee is
promised. RunBinding is a snapshot the new binding replaces, so it is dropped, not
converted.
"""
from django.db import migrations, models


#: The connection kind -> the protocol an endpoint component speaks.
PROTOCOL_OF = {
    "openai": "openai-chat",
    "oip": "oip-v2",
    "a2a": "agent",
    "rest": "rest",
}


def _connection_config(connection) -> dict:
    config: dict = {}
    if connection.kind in ("openai", "oip"):
        config["model"] = connection.model or ""
    elif connection.kind == "a2a":
        config["path"] = connection.path or ""
        config["protocol_version"] = connection.protocol_version or "1.0"
    elif connection.kind == "rest":
        config["path"] = connection.path or ""
        config["method"] = connection.method or "POST"
        config["body_template"] = connection.body_template or {}
        config["response_path"] = connection.response_path or ""
        config["refusal"] = connection.refusal or {}
    if connection.headers:
        config["headers"] = connection.headers
    if connection.secret_header:
        config["secret_header"] = connection.secret_header
    return config


def _rekey(ProjectConfig, project_id, old_key, new_key):
    return ProjectConfig.objects.filter(
        project_id=project_id, category="secrets", key=old_key
    ).update(key=new_key, name=new_key)


def forwards(apps, schema_editor):
    Connection = apps.get_model("aisc_backend", "Connection")
    Helper = apps.get_model("aisc_backend", "Helper")
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    AISystem = apps.get_model("aisc_backend", "AISystem")
    ProjectConfig = apps.get_model("aisc_backend", "ProjectConfig")

    system_of_project = {s.project_id: s.id for s in AISystem.objects.all()}

    for connection in Connection.objects.all():
        component = AIComponent.objects.create(
            name=connection.name,
            description=connection.description or "",
            component_type="endpoint",
            project_id=connection.project_id,
            system_id=system_of_project.get(connection.project_id),
            url=connection.base_url or "",
            protocol=PROTOCOL_OF.get(connection.kind, connection.kind),
            adapter=connection.kind,
            adapter_config=_connection_config(connection),
            data="",
            json_value={},
        )
        new_key = f"component:{component.pid}:key"
        if _rekey(ProjectConfig, connection.project_id, f"connection:{connection.name}:key", new_key):
            AIComponent.objects.filter(pk=component.pk).update(secret_key=new_key)
        prefix = f"connection:{connection.name}:header:"
        for row in ProjectConfig.objects.filter(
            project_id=connection.project_id, category="secrets", key__startswith=prefix
        ):
            header = (row.json_value or {}).get("header") or row.key[len(prefix):]
            new_header_key = f"component:{component.pid}:header:{header}"
            ProjectConfig.objects.filter(pk=row.pk).update(key=new_header_key, name=new_header_key)

    for helper in Helper.objects.all():
        component = AIComponent.objects.create(
            name=helper.name,
            description=helper.description or "",
            component_type="endpoint",
            project_id=helper.project_id,
            system_id=None,
            url=helper.endpoint_url or "",
            protocol="openai-chat",
            adapter="openai",
            adapter_config={},
            data="",
            json_value={},
        )
        new_key = f"component:{component.pid}:key"
        if _rekey(ProjectConfig, helper.project_id, f"helper:{helper.name}:key", new_key):
            AIComponent.objects.filter(pk=component.pk).update(secret_key=new_key)


def backwards(apps, schema_editor):
    """Best-effort: the endpoint components this made are left in place (their data
    needs no preservation guarantee); the recreated tables start empty."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0043_component_endpoint_model"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.DeleteModel(name="RunBinding"),
        migrations.DeleteModel(name="Connection"),
        migrations.DeleteModel(name="Helper"),
    ]
