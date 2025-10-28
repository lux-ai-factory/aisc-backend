from ninja import ModelSchema

from a4s_backend.models.model import Model
from a4s_backend.schemas.dataset import DatasetOutScheme, DatasetPidOutScheme


class ModelPidOutScheme(ModelSchema):
    dataset: DatasetPidOutScheme

    class Meta:
        model = Model
        fields = ["pid"]

class ModelOutScheme(ModelSchema):
    dataset: DatasetOutScheme

    class Meta:
        model = Model
        fields = ["pid", "name", "data"]
