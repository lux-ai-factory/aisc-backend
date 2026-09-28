# Squashed migration for the AISystem feature branch.
#
# Single migration capturing the net effect of the original 0014..0022 series:
#
#   - introduces AISystem / AIComponent / EvaluationInput and merges the legacy
#     Model + Dataset entities into AIComponent;
#   - renames ProjectSetting -> ProjectConfig and
#     PluginConfigSetting -> PluginConfigProjectConfig;
#   - moves LLM/datashape/resource configuration onto AIComponent.json_value and
#     drops the intermediate endpoint_url/secret columns that 0014..0022 first
#     added and then removed;
#   - drops the API_ENDPOINT concept from ProjectConfig (no endpoint_type/url
#     columns; categories are only secrets/variables);
#   - adds the evaluation-created_at timestamp.
#
# Legacy tables are only dropped after their rows have been copied into the new
# entities.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


def migrate_to_ai_system(apps, schema_editor):
    Project = apps.get_model("aisc_backend", "Project")
    AISystem = apps.get_model("aisc_backend", "AISystem")
    AIComponent = apps.get_model("aisc_backend", "AIComponent")
    Dataset = apps.get_model("aisc_backend", "Dataset")
    Model = apps.get_model("aisc_backend", "Model")
    LegacyInput = apps.get_model("aisc_backend", "EvaluationPluginInputFile")
    EvaluationInput = apps.get_model("aisc_backend", "EvaluationInput")

    db_alias = schema_editor.connection.alias

    for project in Project.objects.using(db_alias).all():
        system = AISystem.objects.using(db_alias).create(
            project=project,
            name=f"{project.name} system",
            description="",
        )

        dataset_map = {}
        for ds in Dataset.objects.using(db_alias).filter(project=project):
            component = AIComponent.objects.using(db_alias).create(
                pid=ds.pid,
                name=ds.name,
                description=ds.description,
                created_at=ds.created_at,
                data=ds.data,
                file_size=ds.file_size,
                storage_container=ds.storage_container,
                component_type="dataset",
                system=system,
            )
            dataset_map[ds.id] = component

        model_map = {}
        for mod in Model.objects.using(db_alias).filter(project=project):
            component = AIComponent.objects.using(db_alias).create(
                pid=mod.pid,
                name=mod.name,
                description=mod.description,
                created_at=mod.created_at,
                data=mod.data,
                file_size=mod.file_size,
                storage_container=mod.storage_container,
                component_type="model",
                system=system,
            )
            model_map[mod.id] = component

        for inp in (
            LegacyInput.objects.using(db_alias)
            .select_related("content_type")
            .all()
        ):
            content_type_model = inp.content_type.model
            if content_type_model == "dataset":
                component = dataset_map.get(inp.object_id)
            elif content_type_model == "model":
                component = model_map.get(inp.object_id)
            else:
                component = None

            if component is None:
                continue

            EvaluationInput.objects.using(db_alias).create(
                pid=inp.pid,
                name=inp.name,
                description=inp.description,
                created_at=inp.created_at,
                evaluation_plugin_id=inp.evaluation_plugin_id,
                component=component,
            )


def general_setting_to_variables(apps, schema_editor):
    ProjectConfig = apps.get_model("aisc_backend", "ProjectConfig")
    ProjectConfig.objects.using(schema_editor.connection.alias).filter(
        category="general"
    ).update(category="variables")


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0013_projectsetting_pluginconfigsetting_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AISystem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pid", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("project", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="aisystem", to="aisc_backend.project")),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="AIComponent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pid", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("data", models.CharField(max_length=255)),
                ("file_size", models.BigIntegerField(blank=True, null=True)),
                ("storage_container", models.CharField(choices=[("datasets", "Datasets"), ("models", "Models"), ("artifacts", "Artifacts")], default="datasets", max_length=255)),
                ("component_type", models.CharField(choices=[("dataset", "Dataset"), ("model", "Model / file-backed artifact"), ("llm", "LLM (OpenAI-compatible)"), ("datashape", "DataShape (derived from a dataset)"), ("resource", "Resource / generic reference")], default="model", max_length=50)),
                ("json_value", models.JSONField(blank=True, default=dict)),
                ("source_dataset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="derived_datashapes", to="aisc_backend.aicomponent")),
                ("system", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="components", to="aisc_backend.aisystem")),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="EvaluationInput",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pid", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("value", models.JSONField(blank=True, default=dict)),
                ("component", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="evaluation_inputs", to="aisc_backend.aicomponent")),
                ("evaluation_plugin", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="evaluation_inputs", to="aisc_backend.evaluationplugin")),
            ],
            options={"unique_together": {("evaluation_plugin", "name")}},
        ),
        # ProjectSetting -> ProjectConfig.
        migrations.RenameModel(
            old_name="ProjectSetting",
            new_name="ProjectConfig",
        ),
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
            constraint=models.UniqueConstraint(fields=("project", "category", "key"), name="unique_project_config_key"),
        ),
        migrations.AlterField(
            model_name="ProjectConfig",
            name="project",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="configs", to="aisc_backend.project"),
        ),
        migrations.AlterField(
            model_name="ProjectConfig",
            name="category",
            field=models.CharField(choices=[("secrets", "Secrets / Credentials"), ("variables", "Variables (primitive / JSON)")], max_length=50),
        ),
        # PluginConfigSetting -> PluginConfigProjectConfig.
        migrations.RemoveConstraint(
            model_name="PluginConfigSetting",
            name="unique_plugin_config_setting_key",
        ),
        migrations.RenameModel(
            old_name="PluginConfigSetting",
            new_name="PluginConfigProjectConfig",
        ),
        migrations.RenameField(
            model_name="PluginConfigProjectConfig",
            old_name="plugin_setting_key",
            new_name="plugin_config_key",
        ),
        migrations.AddConstraint(
            model_name="PluginConfigProjectConfig",
            constraint=models.UniqueConstraint(fields=("plugin_config", "plugin_config_key"), name="unique_plugin_config_project_config_key"),
        ),
        migrations.AddField(
            model_name="Evaluation",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        # Copy the legacy data into the new entities, then drop the legacy tables.
        migrations.RunPython(migrate_to_ai_system, migrations.RunPython.noop),
        migrations.RunPython(general_setting_to_variables, migrations.RunPython.noop),
        migrations.DeleteModel(name="Model"),
        migrations.DeleteModel(name="Dataset"),
        migrations.DeleteModel(name="EvaluationPluginInputFile"),
    ]
