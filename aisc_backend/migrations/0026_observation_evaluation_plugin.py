"""Each observation names the run whose measures it holds.

An observation carried only its evaluation and the plugin's tag (`package::name (vX)`), which two runs of one
plugin in one evaluation share. The measures route now records the run. An existing observation gets its run
when its tag matches exactly one run of its evaluation; where two runs share the tag, the observation keeps no
run, since the tag cannot say which one it was, and readers fall back to the tag as before.
"""
import django.db.models.deletion
from django.db import migrations, models


def _tag(plugin) -> str:
    # Plugin.__str__, which the historical model does not have
    return f"{plugin.package_name}::{plugin.name} (v{plugin.version})"


def backfill(apps, schema_editor):
    db = schema_editor.connection.alias
    Observation = apps.get_model("aisc_backend", "Observation")
    EvaluationPlugin = apps.get_model("aisc_backend", "EvaluationPlugin")
    runs_by_tag = {}
    for run in EvaluationPlugin.objects.using(db).select_related("plugin_config__plugin"):
        runs_by_tag.setdefault((run.evaluation_id, _tag(run.plugin_config.plugin)), []).append(run.id)
    for observation in Observation.objects.using(db).filter(evaluation_plugin__isnull=True).only(
            "id", "evaluation_id", "tool"):
        runs = runs_by_tag.get((observation.evaluation_id, observation.tool), [])
        if len(runs) == 1:
            Observation.objects.using(db).filter(pk=observation.pk).update(evaluation_plugin_id=runs[0])


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0025_evaluation_input_helper_ref'),
    ]

    operations = [
        migrations.AddField(
            model_name='observation',
            name='evaluation_plugin',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='observations', to='aisc_backend.evaluationplugin'),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
