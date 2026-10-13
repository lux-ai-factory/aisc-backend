# Squashed from unmerged 0015_evaluationinput_adapter and
# 0016_evaluationinput_adapter_chain: creates the final ordered-chain schema
# directly. The intermediate single-FK state never existed in any release, so
# no data migration is needed.

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0014_ai_system_and_project_config_squashed'),
    ]

    operations = [
        migrations.CreateModel(
            name='InputAdapter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('pid', models.UUIDField(default=uuid.uuid4, editable=False)),
                ('name', models.CharField(max_length=255)),
                ('description', models.CharField(max_length=255)),
                ('adapter_class', models.CharField(max_length=255)),
                ('package_name', models.CharField(blank=True, max_length=255, null=True)),
                ('version', models.CharField(blank=True, max_length=50, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
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
    ]
