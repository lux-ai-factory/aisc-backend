from ninja import ModelSchema, Field

from aisc_backend.models.project import Project
from aisc_backend.schemas.ai_system import AIComponentOutSchema
from aisc_backend.schemas.plugin import PluginOutSchema


class ProjectInSchema(ModelSchema):
    class Meta:
        model = Project
        # The platform project this workspace belongs to, when it was made from
        # one. Optional: the engine can still be used on its own.
        fields = ["name", "platform_project_id"]
        fields_optional = "__all__"

class ProjectOutSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name", "pid"]

class ProjectDetailsOutSchema(ModelSchema):
    components: list[AIComponentOutSchema] = Field([], alias="get_components")
    plugins: list[PluginOutSchema] = Field([], alias="get_plugins")

    class Meta:
        model = Project
        exclude = ["id"]
