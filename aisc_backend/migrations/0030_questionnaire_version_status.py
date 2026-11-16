from django.db import migrations, models


def backfill(apps, schema_editor):
    """Existing versions take their questionnaire's current status; existing
    answers point at that questionnaire's latest version."""
    QuestionnaireVersion = apps.get_model("aisc_backend", "QuestionnaireVersion")
    Qualification = apps.get_model("aisc_backend", "Qualification")
    for version in QuestionnaireVersion.objects.all():
        version.status = version.questionnaire.status
        version.save(update_fields=["status"])
    for qualification in Qualification.objects.exclude(questionnaire_id=""):
        latest = (
            QuestionnaireVersion.objects.filter(questionnaire__pid=qualification.questionnaire_id)
            .order_by("-number")
            .first()
        )
        if latest is not None:
            qualification.questionnaire_version = latest.number
            qualification.save(update_fields=["questionnaire_version"])


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0029_riskversion'),
    ]

    operations = [
        migrations.AddField(
            model_name='qualification',
            name='questionnaire_version',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='questionnaireversion',
            name='status',
            field=models.CharField(default='draft', max_length=32),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
