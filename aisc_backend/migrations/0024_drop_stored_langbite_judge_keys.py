"""Once the place where stored LangBiTe configurations lost their judge fields; now it changes nothing.

LangBiTe's form once asked for the judge (judge_model, judge_api_key), and a configuration saved then kept the
key in plain text. This migration used to remove both fields on upgrade, with no backup. It now changes nothing:
`manage.py clean_old_secrets` does that work behind the cleanup's backup and dry run
(scripts/clean-old-secrets.sh). It stays in the chain so an install that already applied it keeps a consistent
history, and so the next migration has the same parent everywhere.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("aisc_backend", "0023_ledger_outbox")]

    operations = []
