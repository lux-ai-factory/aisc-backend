import uuid

from ninja import ModelSchema, Field

from a4s_backend.models import Measurement
from a4s_backend.schemas.feature import FeatureOutScheme


class MeasureInSchema(ModelSchema):
    feature_pid: uuid.UUID | None = Field(default=None, alias="feature_pid")

    class Meta:
        model = Measurement
        fields = "__all__"
        fields_optional = "__all__"


class MeasureOutSchema(ModelSchema):
    feature: FeatureOutScheme | None

    class Meta:
        model = Measurement
        fields = ["name", "score", "time", "feature"]
