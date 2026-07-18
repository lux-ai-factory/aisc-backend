from __future__ import annotations

from ninja import ModelSchema, Schema

from aisc_backend.models import Dataset


class DatasetPidOutSchema(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["pid", "name"]

class DatasetOutSchema(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["pid", "name", "data", "label_mappings"]

class DatasetInSchema(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["name"]

class DatasetLabelMappingsUpdateSchema(Schema):
    label_mappings: dict
