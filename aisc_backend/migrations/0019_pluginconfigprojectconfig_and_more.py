import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0018_rename_value_category_to_variables"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="pluginconfigsetting",
            name="unique_plugin_config_setting_key",
        ),
        migrations.RenameModel(
            old_name="PluginConfigSetting",
            new_name="PluginConfigProjectConfig",
        ),
        migrations.RenameField(
            model_name="pluginconfigprojectconfig",
            old_name="plugin_setting_key",
            new_name="plugin_config_key",
        ),
        migrations.AddConstraint(
            model_name="pluginconfigprojectconfig",
            constraint=models.UniqueConstraint(
                fields=("plugin_config", "plugin_config_key"),
                name="unique_plugin_config_project_config_key",
            ),
        ),
        migrations.RemoveField(
            model_name="evaluation",
            name="target_system",
        ),
        migrations.RemoveField(
            model_name="evaluation",
            name="target_component",
        ),
        migrations.AlterField(
            model_name="evaluationinput",
            name="evaluation_plugin",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="evaluation_inputs",
                to="aisc_backend.evaluationplugin",
            ),
        ),
    ]
