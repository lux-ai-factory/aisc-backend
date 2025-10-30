from ninja import ModelSchema, Field

from a4s_backend.models.project import Project
from a4s_backend.schemas.dataset import DatasetOutSchema
from a4s_backend.schemas.model import ModelOutSchema


class ProjectInSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name", "frequency", "window_size"]
        fields_optional = "__all__"

class ProjectOutSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name", "frequency", "window_size", "pid"]

class ProjectDetailsOutSchema(ModelSchema):
    datasets: list[DatasetOutSchema] = Field([], alias="get_datasets")
    models: list[ModelOutSchema] = Field([], alias="get_models")

    class Meta:
        model = Project
        exclude = ["id"]
