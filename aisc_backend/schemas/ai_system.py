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
    source_dataset_pid: uuid.UUID | None = None
    #: Filled by the component service row, not a model column.
    has_secret: bool = False
    secret_headers: list[str] = Field(default=[])

    class Meta:
        model = AIComponent
        fields = [
            "pid", "name", "description", "component_type", "data", "file_size",
            "json_value", "format", "url", "protocol", "adapter", "adapter_version",
            "adapter_config", "preset", "secret_key",
        ]


class AIComponentInSchema(Schema):
    """Create or edit a component (ADR 0009).

    A component may be created from a preset (``preset`` + ``fields``) or with the
    advanced payload (``component_type``, ``protocol``, ``url``, ``adapter``,
    ``adapter_config``, ``secret``).
    """

    name: str | None = None
    description: str | None = None
    component_type: str | None = None
    source_dataset_pid: uuid.UUID | None = None
    json_value: dict | None = None

    # endpoint components
    url: str | None = None
    protocol: str | None = None
    adapter: str | None = None
    adapter_version: str | None = None
    adapter_config: dict | None = None
    preset: str | None = None
    #: The preset form's values, keyed by the preset field's key.
    fields: dict | None = None
    #: The key to store; "" clears it. Never read back.
    secret: str | None = None
    #: Or reference an existing project secret by key instead of storing one.
    secret_key: str | None = None
    secret_headers: dict[str, str] | None = None


class AISystemDetailOutSchema(ModelSchema):
    components: list[AIComponentOutSchema] = Field(default=[], alias="get_components")

    class Meta:
        model = AISystem
        fields = ["pid", "name", "description"]
        fields_optional = "__all__"
