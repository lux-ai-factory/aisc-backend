from ninja import ModelSchema

from a4s_backend.models.model import Model
from a4s_backend.schemas.dataset import DatasetOutSchema, DatasetPidOutSchema


class ModelPidOutSchema(ModelSchema):
    dataset: DatasetPidOutSchema

    class Meta:
        model = Model
        fields = ["pid"]

class ModelOutSchema(ModelSchema):
    dataset: DatasetOutSchema

    class Meta:
        model = Model
        fields = ["pid", "name", "data"]
