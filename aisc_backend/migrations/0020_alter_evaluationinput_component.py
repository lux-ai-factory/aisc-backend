import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0019_pluginconfigprojectconfig_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evaluationinput",
            name="component",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inputs",
                to="aisc_backend.aicomponent",
            ),
        ),
    ]
