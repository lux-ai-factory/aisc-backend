from ninja import ModelSchema

from a4s_backend.models.datashape import DataShape
from a4s_backend.schemas.dataset import DatasetOutScheme


class DataShapeOutScheme(ModelSchema):
    features: list
    datasets: list[DatasetOutScheme]

    class Meta:
        model = DataShape
        fields = "__all__"