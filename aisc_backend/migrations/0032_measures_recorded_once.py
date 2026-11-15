"""A test's measures are recorded once, and a metric name is one metric.

Safe on an existing install: two unique rules are added and no row is changed. Before they are added, the existing
metric names and the tests that already have an observation are read; if one of them repeats, the upgrade stops and
names it, because the duplicates have to be merged by an operator action first.
"""
from collections import Counter

from django.db import migrations, models


def refuse_duplicates(values: list[str], what: str) -> None:
    """Stops the upgrade, naming them, when some of the values repeat."""
    dupes = {value for value, count in Counter(values).items() if count > 1}
    if dupes:
        raise RuntimeError(f"{what} are not unique: {sorted(dupes)!r}; "
                           "merge them by an operator action before upgrading")


def check_existing(apps, schema_editor):
    alias = schema_editor.connection.alias
    Metric = apps.get_model("aisc_backend", "Metric")
    Observation = apps.get_model("aisc_backend", "Observation")
    refuse_duplicates(list(Metric.objects.using(alias).values_list("name", flat=True)), "metric names")
    tests = Observation.objects.using(alias).exclude(evaluation_plugin=None).values_list("evaluation_plugin_id",
                                                                                          flat=True)
    refuse_duplicates([str(test) for test in tests], "tests with an observation")


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0031_component_file_hash"),
    ]

    operations = [
        migrations.RunPython(check_existing, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="metric",
            constraint=models.UniqueConstraint(fields=("name",), name="one_metric_per_name"),
        ),
        migrations.AddConstraint(
            model_name="observation",
            constraint=models.UniqueConstraint(condition=models.Q(("evaluation_plugin__isnull", False)),
                                               fields=("evaluation_plugin",), name="one_observation_per_test"),
        ),
    ]
