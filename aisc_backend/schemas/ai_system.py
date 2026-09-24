from ninja import ModelSchema, Field, Schema
import uuid

from aisc_backend.models.ai_system import AISystem, AIComponent


class AISystemOutSchema(ModelSchema):
    class Meta:
        model = AISystem
        fields = ["pid", "name", "description"]


class AIComponentPidOutSchema(ModelSchema):
    class Meta:
        model = AIComponent
        fields = ["pid"]


class AIComponentOutSchema(ModelSchema):
    source_dataset_pid: uuid.UUID | None = Field(default=None, alias="source_dataset.pid")

    class Meta:
        model = AIComponent
        fields = ["pid", "name", "description", "component_type", "data",
                  "file_size", "json_value"]


class AIComponentInSchema(Schema):
    name: str | None = None
    description: str | None = None
    component_type: str | None = None
    source_dataset_pid: uuid.UUID | None = None
    json_value: dict | None = None


class AISystemDetailOutSchema(ModelSchema):
    components: list[AIComponentOutSchema] = Field(default=[], alias="get_components")

    class Meta:
        model = AISystem
        fields = ["pid", "name", "description"]
        fields_optional = "__all__"
