import uuid

from ninja import ModelSchema, Field

from vera_backend.models import Measurement
from vera_backend.schemas.feature import FeatureOutSchema


class MeasureInSchema(ModelSchema):
    feature_pid: uuid.UUID | None = Field(default=None, alias="feature_pid")

    class Meta:
        model = Measurement
        fields = "__all__"
        fields_optional = "__all__"


class MeasureOutSchema(ModelSchema):
    feature: FeatureOutSchema | None

    class Meta:
        model = Measurement
        fields = ["name", "score", "time", "feature", "description"]
