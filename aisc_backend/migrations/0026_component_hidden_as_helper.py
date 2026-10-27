"""An `llm` component older runs used as a helper can be moved to the platform's helper models.

The row is kept (the runs' inputs point at it) and marked with the reference it moved to; every
existing component has none, so nothing changes for them.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0025_evaluation_input_helper_ref'),
    ]

    operations = [
        migrations.AddField(
            model_name='aicomponent',
            name='hidden_as_helper',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
