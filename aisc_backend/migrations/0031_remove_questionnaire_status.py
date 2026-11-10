from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0030_questionnaire_version_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='questionnaire',
            name='status',
        ),
        migrations.RemoveField(
            model_name='questionnaireversion',
            name='status',
        ),
    ]
