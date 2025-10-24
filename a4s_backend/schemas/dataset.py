from __future__ import annotations

import uuid

from ninja import ModelSchema, Schema, Field

from a4s_backend.models import Dataset, DataShape
from a4s_backend.schemas.feature import FeatureOutScheme

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

class DatasetPidOutScheme(ModelSchema):
    shape: DataShapeOutScheme = Field(default=[], alias="get_datashape")

    class Meta:
        model = Dataset
        fields = ["pid", "name"]

class DatasetOutScheme(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["pid", "name", "data"]

class DatasetInScheme(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["name"]
