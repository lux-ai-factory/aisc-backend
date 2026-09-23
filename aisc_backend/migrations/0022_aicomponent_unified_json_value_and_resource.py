from django.db import migrations, models


def backfill_llm_json(apps, schema_editor):
    """Move llm endpoint/secret columns into the typed `json_value` LLMConfig."""
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    for component in AIComponent.objects.filter(component_type="llm"):
        payload = dict(component.json_value or {})
        payload.setdefault("endpoint_url", component.endpoint_url or "")
        payload.setdefault(
            "secret_key",
            component.secret.key if component.secret_id else "",
        )
        component.json_value = payload
        component.save(update_fields=["json_value"])


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0021_llm_inputs_and_evaluation_created_at"),
    ]

    operations = [
        migrations.RunPython(backfill_llm_json, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="aicomponent",
            name="endpoint_url",
        ),
        migrations.RemoveField(
            model_name="aicomponent",
            name="secret",
        ),
        migrations.AlterField(
            model_name="aicomponent",
            name="component_type",
            field=models.CharField(
                choices=[
                    ("dataset", "Dataset"),
                    ("model", "Model / file-backed artifact"),
                    ("llm", "LLM (OpenAI-compatible)"),
                    ("datashape", "DataShape (derived from a dataset)"),
                    ("resource", "Resource / generic reference"),
                ],
                default="model",
                max_length=50,
            ),
        ),
    ]
