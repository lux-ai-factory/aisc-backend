# Generated for adapter chaining: ordered M2M through EvaluationInputAdapter.

import django.db.models.deletion
from django.db import migrations, models


def copy_single_adapter_to_chain(apps, schema_editor):
    EvaluationInput = apps.get_model("aisc_backend", "EvaluationInput")
    EvaluationInputAdapter = apps.get_model("aisc_backend", "EvaluationInputAdapter")
    for inp in EvaluationInput.objects.exclude(adapter__isnull=True).only("id", "adapter"):
        EvaluationInputAdapter.objects.get_or_create(
            evaluation_input_id=inp.id,
            order=0,
            defaults={"adapter_id": inp.adapter_id},
        )


def copy_chain_to_single_adapter(apps, schema_editor):
    # Reverse migration: restore the first adapter of each chain into the FK.
    EvaluationInput = apps.get_model("aisc_backend", "EvaluationInput")
    EvaluationInputAdapter = apps.get_model("aisc_backend", "EvaluationInputAdapter")
    for link in EvaluationInputAdapter.objects.filter(order=0).only("evaluation_input_id", "adapter_id"):
        EvaluationInput.objects.filter(id=link.evaluation_input_id).update(adapter_id=link.adapter_id)


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0015_evaluationinput_adapter'),
    ]

    operations = [
        migrations.CreateModel(
            name='EvaluationInputAdapter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveIntegerField(default=0)),
                ('adapter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='input_links', to='aisc_backend.inputadapter')),
                ('evaluation_input', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='adapter_links', to='aisc_backend.evaluationinput')),
            ],
            options={
                'ordering': ['order'],
            },
        ),
        migrations.AddConstraint(
            model_name='evaluationinputadapter',
            constraint=models.UniqueConstraint(fields=('evaluation_input', 'order'), name='unique_evaluation_input_adapter_order'),
        ),
        migrations.AddField(
            model_name='evaluationinput',
            name='adapters',
            field=models.ManyToManyField(blank=True, related_name='evaluation_inputs', through='aisc_backend.EvaluationInputAdapter', to='aisc_backend.inputadapter'),
        ),
        migrations.RunPython(copy_single_adapter_to_chain, copy_chain_to_single_adapter),
        migrations.RemoveField(
            model_name='evaluationinput',
            name='adapter',
        ),
    ]
