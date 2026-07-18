from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0010_plugin_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='label_mappings',
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
