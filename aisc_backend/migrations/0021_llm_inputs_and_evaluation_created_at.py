import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0020_alter_evaluationinput_component"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluationinput",
            name="value",
            field=models.JSONField(blank=True, default=dict),
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
                ],
                default="model",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="evaluation",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
    ]
