from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('aisc_backend', '0009_alter_measurement_dimensions'),
    ]

    operations = [
        migrations.AddField(
            model_name='plugin',
            name='enabled',
            field=models.BooleanField(default=True),
        ),
    ]