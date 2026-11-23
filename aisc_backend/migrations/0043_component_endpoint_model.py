import django.db.models.deletion
from django.db import migrations, models


def _suffix(name: str) -> str:
    if not name or "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def forwards(apps, schema_editor):
    """Give every component a project, derive a file format, and turn each old
    ``llm`` component into an ``endpoint`` (protocol ``openai-chat``)."""
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    AISystem = apps.get_model("aisc_backend", "AISystem")
    project_of_system = {s.id: s.project_id for s in AISystem.objects.all()}

    for component in AIComponent.objects.all():
        if component.project_id is None:
            component.project_id = project_of_system.get(component.system_id)

        if component.component_type in ("dataset", "model"):
            component.format = _suffix(component.data or "")

        if component.component_type == "llm":
            value = component.json_value or {}
            component.component_type = "endpoint"
            component.protocol = "openai-chat"
            component.adapter = "openai"
            component.preset = "llm"
            component.url = str(value.get("endpoint_url") or "")
            component.secret_key = str(value.get("secret_key") or "")
            component.json_value = {}

        component.save()


def backwards(apps, schema_editor):
    """Restore each openai endpoint that came from an ``llm`` component."""
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    for component in AIComponent.objects.filter(component_type="endpoint", adapter="openai"):
        component.component_type = "llm"
        component.json_value = {
            "endpoint_url": component.url,
            "secret_key": component.secret_key,
            "model": "",
        }
        component.save()


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0042_alter_runbinding_evaluation_plugin_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="aicomponent",
            name="project",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="components",
                to="aisc_backend.project",
            ),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="format",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="protocol",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="adapter",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="adapter_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="adapter_config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="preset",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="secret_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="aicomponent",
            name="component_type",
            field=models.CharField(
                choices=[
                    ("dataset", "Dataset"),
                    ("model", "Model / file-backed artifact"),
                    ("datashape", "DataShape (derived from a dataset)"),
                    ("resource", "Resource / generic reference"),
                    ("endpoint", "Endpoint"),
                    ("llm", "LLM (legacy, migrated to endpoint)"),
                ],
                default="model",
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="aicomponent",
            name="system",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="components",
                to="aisc_backend.aisystem",
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="aicomponent",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="components",
                to="aisc_backend.project",
            ),
        ),
    ]
