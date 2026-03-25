from ninja import ModelSchema, Field

from a4s_backend.models.project import Project
from a4s_backend.schemas.dataset import DatasetOutSchema
from a4s_backend.schemas.model import ModelOutSchema
from a4s_backend.schemas.plugin import PluginOutSchema


class ProjectInSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name"]
        fields_optional = "__all__"

class ProjectOutSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name", "pid"]

class ProjectDetailsOutSchema(ModelSchema):
    datasets: list[DatasetOutSchema] = Field([], alias="get_datasets")
    models: list[ModelOutSchema] = Field([], alias="get_models")
    plugins: list[PluginOutSchema] = Field([], alias="get_enabled_plugins")

    class Meta:
        model = Project
        exclude = ["id"]
