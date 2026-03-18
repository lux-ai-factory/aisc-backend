from ninja import ModelSchema
from pydantic import Field

from a4s_backend.models import Plugin, EvaluationPlugin, PluginConfig


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
    name: str = Field(alias="plugin.name")
    plugin_config: PluginConfigOutSchema | None = Field(default=None, alias="plugin_config")
    dataset_filename: str | None = Field(default=None, alias="dataset.data")
    model_filename: str | None = Field(default=None, alias="model.data")

    class Meta:
        model = EvaluationPlugin
        fields = "__all__"
        fields_optional = "__all__"
