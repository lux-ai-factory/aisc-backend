# Rename ProjectSetting -> ProjectConfig and GENERAL -> VALUE.
# Uses RenameModel/RenameField so existing rows and references are preserved.

from django.db import migrations, models


def rename_general_category(apps, schema_editor):
    ProjectConfig = apps.get_model("aisc_backend", "ProjectConfig")
    ProjectConfig.objects.using(schema_editor.connection.alias).filter(
        category="general"
    ).update(category="value")


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0015_aicomponent_endpoint_url_aicomponent_json_value_and_more"),
    ]

    operations = [
        migrations.RenameModel("ProjectSetting", "ProjectConfig"),
        migrations.RenameField(
            model_name="PluginConfig",
            old_name="project_settings",
            new_name="project_configs",
        ),
        migrations.RenameField(
            model_name="PluginConfigSetting",
            old_name="project_setting",
            new_name="project_config",
        ),
        migrations.RemoveConstraint(
            model_name="ProjectConfig",
            name="unique_project_setting_key",
        ),
        migrations.AddConstraint(
            model_name="ProjectConfig",
            constraint=models.UniqueConstraint(
                fields=("project", "category", "key"),
                name="unique_project_config_key",
            ),
        ),
        migrations.AlterField(
            model_name="ProjectConfig",
            name="category",
            field=models.CharField(
                choices=[
                    ("secrets", "Secrets / Credentials"),
                    ("value", "Plugin value (primitive / JSON)"),
                    ("datashape", "DataShape / Feature Definition"),
                    ("api_endpoint", "API Endpoint"),
                ],
                max_length=50,
            ),
        ),
        migrations.RunPython(rename_general_category, migrations.RunPython.noop),
    ]
