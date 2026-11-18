from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0040_system_aisystem"),
    ]

    operations = [
        migrations.AddField(
            model_name="qualification",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
