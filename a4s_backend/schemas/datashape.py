import uuid

from ninja import ModelSchema, Field, Schema

from a4s_backend.models.datashape import DataShape
from a4s_backend.schemas.dataset import DatasetOutScheme
from a4s_backend.schemas.feature import FeatureInScheme, FeatureOutScheme


class BlankFeatureSchema(Schema):
    name: str = ""
    pid: str = ""

class DataShapeOutScheme(ModelSchema):
    features: list[FeatureOutScheme] = Field(default=[], alias="get_features")
    dataset: DatasetOutScheme
    dataset_pid: uuid.UUID = Field(default=None, alias="dataset.pid")
    date: FeatureOutScheme | BlankFeatureSchema = Field(default=BlankFeatureSchema(), alias="date_feature")
    target: FeatureOutScheme | BlankFeatureSchema = Field(default=BlankFeatureSchema(), alias="target_feature")

    class Meta:
        model = DataShape
        fields = "__all__"

class DataShapeInScheme(ModelSchema):
    features: list[FeatureInScheme]
    date: FeatureInScheme | None
    target: FeatureInScheme | None

    class Meta:
        model = DataShape
        fields = "__all__"
        fields_optional = "__all__"
