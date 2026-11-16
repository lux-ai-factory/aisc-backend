"""An artifact says whether it holds a conversation with the system under test; such an artifact reaches the
project's owners and editors only (conversation_artifacts.py).

Existing rows are marked by the name rule (conversation, transcript, calls, chat or execution.log in the name),
which covers the worker's two files of every earlier run. Safe on an existing install: one column with a
default, and a mark on the rows whose name says so; nothing else changes.
"""
from django.db import migrations, models


def mark_existing(apps, schema_editor):
    from aisc_backend.conversation_artifacts import named_like_one

    Artifact = apps.get_model("aisc_backend", "Artifact")
    marked = [a.pk for a in Artifact.objects.only("pk", "name").iterator() if named_like_one(a.name)]
    if marked:
        Artifact.objects.filter(pk__in=marked).update(holds_conversation=True)


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0038_runs_finished_before"),
    ]

    operations = [
        migrations.AddField(
            model_name="artifact",
            name="holds_conversation",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing, migrations.RunPython.noop),
    ]
