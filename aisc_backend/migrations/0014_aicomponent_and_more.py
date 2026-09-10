# Hand-crafted migration: introduce AISystem/AIComponent/EvaluationInput and
# merge the legacy Model + Dataset entities into AIComponent.
#
# Order matters here: we keep the legacy Dataset/Model/EvaluationPluginInputFile
# tables alive until after the RunPython data migration has copied their rows
# into the new entities.

import django.db.models.deletion
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


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0013_projectsetting_pluginconfigsetting_and_more"),
    ]

    operations = [
        # New entities (schema-only first; data copied below).
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
                ("component_type", models.CharField(choices=[("dataset", "Dataset"), ("model", "Model / file-backed artifact"), ("llm", "LLM (OpenAI-compatible)"), ("rest", "REST endpoint")], default="model", max_length=50)),
            ],
            options={"abstract": False},
        ),
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
            name="EvaluationInput",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pid", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("component", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="evaluation_inputs", to="aisc_backend.aicomponent")),
                ("evaluation_plugin", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="input_files", to="aisc_backend.evaluationplugin")),
            ],
            options={"unique_together": {("evaluation_plugin", "name")}},
        ),
        migrations.AddField(
            model_name="aicomponent",
            name="system",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="components", to="aisc_backend.aisystem"),
        ),
        # ProjectSetting API_ENDPOINT support.
        migrations.AddField(
            model_name="projectsetting",
            name="endpoint_type",
            field=models.CharField(blank=True, choices=[("openai_compatible", "OpenAI-compatible"), ("rest", "REST")], default="", max_length=50),
        ),
        migrations.AddField(
            model_name="projectsetting",
            name="url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="projectsetting",
            name="category",
            field=models.CharField(choices=[("secrets", "Secrets"), ("datashape", "DataShape / Feature Definition"), ("general", "General Setting"), ("api_endpoint", "API Endpoint")], max_length=50),
        ),
        # Evaluation run-targets.
        migrations.AddField(
            model_name="evaluation",
            name="target_component",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="evaluations", to="aisc_backend.aicomponent"),
        ),
        migrations.AddField(
            model_name="evaluation",
            name="target_system",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="evaluations", to="aisc_backend.aisystem"),
        ),
        # Legacy table constraints (relax before copying rows, keep tables).
        migrations.AlterUniqueTogether(
            name="evaluationplugininputfile",
            unique_together=None,
        ),
        # Copy legacy data into the new entities.
        migrations.RunPython(migrate_to_ai_system, migrations.RunPython.noop),
        # Drop the merged legacy tables.
        migrations.DeleteModel(name="Model"),
        migrations.DeleteModel(name="Dataset"),
        migrations.DeleteModel(name="EvaluationPluginInputFile"),
    ]
