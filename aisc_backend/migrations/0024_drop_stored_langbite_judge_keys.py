"""No stored LangBiTe configuration keeps the judge's key.

LangBiTe's form once asked for the judge (judge_model, judge_api_key), and a configuration saved then
kept the key in plain text. The judge is now a helper model picked in the evaluation form: the form no
longer has those fields, so nothing could clear them. This removes both from every stored LangBiTe
configuration; other plugins' configurations are left as they are. Going back restores nothing: the
key is not kept anywhere to restore. Values are never logged.
"""
from django.db import migrations

PACKAGE = "aisc-plugin-langbite"
DROPPED = ("judge_api_key", "judge_model")


def forwards(apps, schema_editor):
    PluginConfig = apps.get_model("aisc_backend", "PluginConfig")
    using = schema_editor.connection.alias
    for saved in PluginConfig.objects.using(using).filter(plugin__package_name=PACKAGE).only("pk", "config"):
        config = saved.config
        if not isinstance(config, dict) or not any(key in config for key in DROPPED):
            continue
        saved.config = {key: value for key, value in config.items() if key not in DROPPED}
        saved.save(using=using, update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [("aisc_backend", "0023_ledger_outbox")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
