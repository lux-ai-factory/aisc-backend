import math
import uuid

from a4s_backend.models import FeatureType
from a4s_backend.schemas.feature import FeatureInSchema


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