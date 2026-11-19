from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0032_seed_objectives'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectreportsettings',
            name='report_title',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='projectreportsettings',
            name='subtitle',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='projectreportsettings',
            name='primary_color',
            field=models.CharField(default='#000fdf', max_length=7),
        ),
        migrations.AddField(
            model_name='projectreportsettings',
            name='accent_color',
            field=models.CharField(default='#ff007e', max_length=7),
        ),
    ]
