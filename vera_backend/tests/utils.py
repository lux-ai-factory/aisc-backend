import datetime
import math
import random
import uuid

from vera_backend.models import FeatureType
from vera_backend.schemas.datashape import DataShapeOutSchema
from vera_backend.schemas.feature import FeatureInSchema, FeatureOutSchema
from vera_backend.schemas.measure import MeasureInSchema

def create_measure_in_schema_list_for_model_eval(batch_count: int, day_int: int) -> list[MeasureInSchema]:
    measures = []
    measures.extend(_create_measure_in_schema_list(batch_count, day_int, "Accuracy"))
    measures.extend(_create_measure_in_schema_list(batch_count, day_int, "F1"))
    measures.extend(_create_measure_in_schema_list(batch_count, day_int, "Precision"))
    measures.extend(_create_measure_in_schema_list(batch_count, day_int, "Recall"))
    measures.extend(_create_measure_in_schema_list(batch_count, day_int, "MCC"))
    measures.extend(_create_measure_in_schema_list(batch_count, day_int, "ROCAUC"))
    return measures

def create_measure_in_schema_list_for_datashape(datashape: DataShapeOutSchema, batch_count: int, day_int: int, measure_name: str) -> list[MeasureInSchema]:
    measures = []
    for feature in datashape.features:
        measures.extend(_create_measure_in_schema_list(batch_count, day_int, measure_name, feature))

    return measures

def _create_measure_in_schema_list(batch_count: int, day_int: int, measure_name: str, feature: FeatureOutSchema | None = None) -> list[MeasureInSchema]:
    measures = []
    date_today = datetime.date.today()
    min_value = 0.0
    max_value = 1.0
    feature_pid = None

    if feature is not None:
        min_value = feature['min_value']
        max_value = feature['max_value']

    if feature is not None:
        feature_pid = feature['pid']

    for i in range(batch_count):
        measure_date = date_today - datetime.timedelta(days=i * day_int)
        score = random.uniform(min_value, max_value)
        measure_in_schema = MeasureInSchema(time=measure_date, name=measure_name, feature_pid=feature_pid, score=score)
        measures.append(measure_in_schema)

    return measures

def create_feature_in_schema_list() -> list[FeatureInSchema]:
    features: list[FeatureInSchema] = []

    date_feature = FeatureInSchema()
    feature_type = FeatureType.Date
    date_feature.pid = uuid.uuid4()
    date_feature.name = f"{feature_type} feature"
    date_feature.feature_type = feature_type
    date_feature.min_value = 0
    date_feature.max_value = 0
    features.append(date_feature)

    for i in range(15):
        feature = FeatureInSchema()
        feature_type = FeatureType.Integer
        feature.pid = uuid.uuid4()
        feature.name = f"{feature_type} feature {i}"
        feature.feature_type = feature_type
        feature.min_value = 0
        feature.max_value = i
        features.append(feature)

    for i in range(15):
        feature = FeatureInSchema()
        feature_type = FeatureType.Float
        feature.pid = uuid.uuid4()
        feature.name = f"{feature_type} feature {i}"
        feature.feature_type = feature_type
        feature.min_value = 0.0
        feature.max_value = i * math.pi
        features.append(feature)

    return features