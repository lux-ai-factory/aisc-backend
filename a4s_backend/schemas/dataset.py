from ninja import ModelSchema

from a4s_backend.models.dataset import Dataset


class DatasetOutScheme(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["pid", "name", "data"]

class DatasetInScheme(ModelSchema):
    class Meta:
        model = Dataset
        fields = ["name"]