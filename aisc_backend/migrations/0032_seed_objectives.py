"""Seed the default O1..O50 control-objectives catalogue.

The original seed migration was emptied (the vendored CSV is gone), so fresh
databases had no default control objectives. This re-seeds the O1..O50 rows
every environment expects (project NULL = the shared, read-only catalogue)
from the AI-Act control-objectives CSV reused from the unified-modules branch
(aisc_backend/data/ai_act_control_objectives.csv).
"""
import csv
from pathlib import Path

from django.db import migrations

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "ai_act_control_objectives.csv"

ROW_FIELDS = {
    "ID": "objective_id",
    "Macro_Requirement": "macro_requirement",
    "Legal_Basis": "legal_basis",
    "Sub_Requirement_Label": "sub_requirement_label",
    "Control_Objective": "text",
    "Assessment_Mode": "assessment_mode",
    "Target": "target",
    "Standards_Grounding": "standards_grounding",
    "Grounding_Tier_Flag": "grounding_tier_flag",
    "Notes": "notes",
}


def seed_objectives(apps, schema_editor):
    ControlObjective = apps.get_model("aisc_backend", "ControlObjective")
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ControlObjective.objects.update_or_create(
                objective_id=row["ID"].strip(),
                defaults={model_field: (row[csv_col] or "").strip()
                          for csv_col, model_field in ROW_FIELDS.items()},
            )


def unseed_objectives(apps, schema_editor):
    ControlObjective = apps.get_model("aisc_backend", "ControlObjective")
    ControlObjective.objects.filter(project__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("aisc_backend", "0031_remove_questionnaire_status"),
    ]

    operations = [
        migrations.RunPython(seed_objectives, unseed_objectives),
    ]
