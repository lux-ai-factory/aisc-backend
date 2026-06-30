# Generated manually to add direction field to Measurement

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("aisc_backend", "0010_plugin_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="measurement",
            name="direction",
            field=models.CharField(blank=True, default="", max_length=50, null=True),
        ),
    ]
