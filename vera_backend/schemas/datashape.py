import uuid

from ninja import ModelSchema, Field, Schema

from vera_backend.models.datashape import DataShape
from vera_backend.schemas.dataset import DatasetOutSchema
from vera_backend.schemas.feature import FeatureInSchema, FeatureOutSchema


class BlankFeatureSchema(Schema):
    name: str = ""
    pid: str = ""

class DataShapeOutSchema(ModelSchema):
    features: list[FeatureOutSchema] = Field(default=[], alias="get_features")
    dataset: DatasetOutSchema
    dataset_pid: uuid.UUID = Field(default=None, alias="dataset.pid")
    date: FeatureOutSchema | BlankFeatureSchema = Field(default=BlankFeatureSchema(), alias="date_feature")
    target: FeatureOutSchema | BlankFeatureSchema = Field(default=BlankFeatureSchema(), alias="target_feature")

    class Meta:
        model = DataShape
        fields = "__all__"

class DataShapeInSchema(ModelSchema):
    features: list[FeatureInSchema]
    date: FeatureInSchema | None
    target: FeatureInSchema | None

    class Meta:
        model = DataShape
        fields = "__all__"
        fields_optional = "__all__"
