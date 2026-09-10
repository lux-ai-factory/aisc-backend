# Rename ProjectConfig category 'value' -> 'variables' (mirrors GitHub secrets/variables).

from django.db import migrations, models


def rename_value_category(apps, schema_editor):
    ProjectConfig = apps.get_model("aisc_backend", "ProjectConfig")
    ProjectConfig.objects.using(schema_editor.connection.alias).filter(
        category="value"
    ).update(category="variables")


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0017_alter_projectconfig_project"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ProjectConfig",
            name="category",
            field=models.CharField(
                choices=[
                    ("secrets", "Secrets / Credentials"),
                    ("variables", "Variables (primitive / JSON)"),
                    ("datashape", "DataShape / Feature Definition"),
                    ("api_endpoint", "API Endpoint"),
                ],
                max_length=50,
            ),
        ),
        migrations.RunPython(rename_value_category, migrations.RunPython.noop),
    ]
