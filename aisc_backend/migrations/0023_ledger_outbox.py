"""The outbox of the engine's ledger events (aisc_backend.ledger): a new table, nothing else changes."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aisc_backend', '0022_one_system_target_per_system'),
    ]

    operations = [
        migrations.CreateModel(
            name='LedgerOutbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('project_pid', models.UUIDField()),
                ('event', models.JSONField()),
                ('queued_at', models.DateTimeField(auto_now_add=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('refused', models.IntegerField(blank=True, null=True)),
            ],
        ),
    ]
