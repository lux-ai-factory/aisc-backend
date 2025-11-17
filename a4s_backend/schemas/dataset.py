from __future__ import annotations

import uuid

from ninja import ModelSchema, Schema, Field

from a4s_backend.models import Dataset, DataShape
from a4s_backend.schemas.feature import FeatureOutSchema

class BlankFeatureSchema(Schema):
    name: str = ""
    pid: str = ""

class DataShapeOutSchema(ModelSchema):
    features: list[FeatureOutSchema] = Field(default=[], alias="get_features")
    dataset: DatasetOutSchema
    dataset_pid: uuid.UUID = Field(default=None, alias="dataset.pid")
    date: FeatureOutSchema | None = Field(default=None, alias="date_feature")
    target: FeatureOutSchema | None = Field(default=None, alias="target_feature")

    class Meta:
        model = DataShape
        fields = "__all__"

class DatasetPidOutSchema(ModelSchema):
    shape: DataShapeOutSchema = Field(default=[], alias="get_datashape")

    class Meta:
        model = Dataset
        fields = ["pid", "name"]

class DatasetOutSchema(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["pid", "name", "data"]

class DatasetInSchema(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["name"]
