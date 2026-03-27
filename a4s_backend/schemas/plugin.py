from typing import Union

from ninja import ModelSchema, Schema
from pydantic import Field

from a4s_backend.models import Plugin, EvaluationPlugin, PluginConfig
from a4s_backend.schemas.dataset import DatasetOutSchema
from a4s_backend.schemas.model import ModelOutSchema


class EvaluationPluginInputFileOutSchema(Schema):
    name: str
    input_type: str
    input_file: Union[DatasetOutSchema, ModelOutSchema]

    @staticmethod
    def resolve_input_type(obj):
        return obj.content_type.model

    @staticmethod
    def resolve_input_file(obj):
        return obj.content_object

class PluginConfigOutSchema(ModelSchema):
    class Meta:
        model = PluginConfig
        fields = ["id", "config", "created_at"]


class PluginOutSchema(ModelSchema):
    config: dict | None = Field(None, alias="current_config.config")

    class Meta:
        model = Plugin
        fields = ["pid", "name"]


class EvaluationPluginOutSchema(ModelSchema):
    name: str = Field(alias="plugin_config.plugin.name")
    plugin_config: PluginConfigOutSchema | None = Field(default=None, alias="plugin_config")
    input_files: list[EvaluationPluginInputFileOutSchema] = Field(default=[], alias="input_files")

    class Meta:
        model = EvaluationPlugin
        fields = "__all__"
        fields_optional = "__all__"
