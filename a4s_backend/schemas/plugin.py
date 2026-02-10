from ninja import ModelSchema
from pydantic import Field

from a4s_backend.models import Plugin, EvaluationPlugin


class PluginOutSchema(ModelSchema):
    class Meta:
        model = Plugin
        fields = ["pid", "name", "config"]


class EvaluationPluginOutSchema(ModelSchema):
    name: str = Field(alias="plugin.name")
    config: dict | None = Field(default=None, alias="plugin.config")
    dataset_filename: str | None = Field(default=None, alias="dataset.data")
    model_filename: str | None = Field(default=None, alias="model.data")
    evaluation_config: dict | None = Field(alias="evaluation_config")

    class Meta:
        model = EvaluationPlugin
        fields = "__all__"
        fields_optional = "__all__"
