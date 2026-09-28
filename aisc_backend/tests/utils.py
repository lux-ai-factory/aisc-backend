import datetime
import random

from django.utils import timezone

from aisc_backend.schemas.measure import MeasureInSchema


def create_measure_in_schema_list_for_model_eval(batch_count: int, day_int: int) -> list[MeasureInSchema]:
    measures: list[MeasureInSchema] = []
    for measure_name in ["Accuracy", "F1", "Precision", "Recall", "MCC", "ROCAUC"]:
        measures.extend(_create_measure_in_schema_list(batch_count, day_int, measure_name))
    return measures


def _create_measure_in_schema_list(batch_count: int, day_int: int, measure_name: str) -> list[MeasureInSchema]:
    measures: list[MeasureInSchema] = []
    date_today = timezone.now()
    for i in range(batch_count):
        measure_date = date_today - datetime.timedelta(days=i * day_int)
        score = random.uniform(0.0, 1.0)
        measures.append(MeasureInSchema(time=measure_date, name=measure_name, score=score))
    return measures
