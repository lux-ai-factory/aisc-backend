from __future__ import annotations

from ninja import ModelSchema

from vera_backend.models import Dataset


class DatasetPidOutSchema(ModelSchema):
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
