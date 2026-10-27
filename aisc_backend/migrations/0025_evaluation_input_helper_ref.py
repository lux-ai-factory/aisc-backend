"""A run's input names either a component of the AI system or a helper model of the platform.

In the Configurator a helper model lives on the platform, so its input carries the reference
(`helper:<platform pid>/<name>`) and no component. Every existing input has a component, so the
check holds for them as they are.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0024_drop_stored_langbite_judge_keys'),
    ]

    operations = [
        migrations.AddField(
            model_name='evaluationinput',
            name='helper_ref',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AlterField(
            model_name='evaluationinput',
            name='component',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='evaluation_inputs', to='aisc_backend.aicomponent'),
        ),
        migrations.AddConstraint(
            model_name='evaluationinput',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('component__isnull', False), ('helper_ref__isnull', True)), models.Q(('component__isnull', True), ('helper_ref__isnull', False)), _connector='OR'), name='evaluation_input_component_or_helper_ref'),
        ),
    ]
