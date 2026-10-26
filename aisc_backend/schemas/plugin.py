import uuid

from ninja import ModelSchema, Schema
from pydantic import Field

from aisc_backend.models import Plugin, EvaluationPlugin, PluginConfig
from aisc_backend.schemas.ai_system import AIComponentOutSchema
from aisc_backend.schemas.project_config import ProjectConfigSelectionSchema


class EvaluationInputOutSchema(Schema):
    name: str
    input_type: str
    input_file: AIComponentOutSchema
    value: dict = Field(default={})

    @staticmethod
    def resolve_input_type(obj):
        return obj.component.component_type

    @staticmethod
    def resolve_input_file(obj):
        return obj.component

class PluginConfigOutSchema(ModelSchema):
    project_config_selections: list[ProjectConfigSelectionSchema] = Field(default=[])

    @staticmethod
    def resolve_project_config_selections(obj):
        return [
            ProjectConfigSelectionSchema(
                plugin_config_key=mapping.plugin_config_key,
                project_config_pid=mapping.project_config.pid,
            )
            for mapping in getattr(obj, "_prefetched_objects_cache", {}).get("setting_mappings", [])
        ]

    class Meta:
        model = PluginConfig
        fields = ["id", "config", "created_at"]


class ShownPluginConfigOutSchema(PluginConfigOutSchema):
    """A stored plugin configuration as a public route answers it: masked as every other read route masks
    it (secret fields and kept paths set, key-shaped strings set). The worker's route keeps the parent."""
    config: dict | None = None

    @staticmethod
    def resolve_config(obj):
        from aisc_backend.routers.plugin import _kept_paths, _plugin_form, _shown_config  # imports this module
        mappings = getattr(obj, "_prefetched_objects_cache", {}).get("setting_mappings", [])
        return _shown_config(obj.config, *_plugin_form(obj.plugin), _kept_paths(mappings))


class PluginOutSchema(ModelSchema):
    config: dict | None = None
    pid: uuid.UUID

    @staticmethod
    def resolve_config(obj):
        """The current configuration with every key set, None when the plugin has none."""
        from aisc_backend.routers.plugin import shown_current_config  # the plugin router imports this module
        return shown_current_config(obj)

    class Meta:
        model = Plugin
        fields = ["pid", "name", "package_name", "version", "display_name", "enabled",
                  "catalogue_slug"]


class EvaluationPluginOutSchema(ModelSchema):
    name: str = Field(alias="plugin_config.plugin.name")
    package_name: str = Field(alias="plugin_config.plugin.package_name")
    version: str = Field(alias="plugin_config.plugin.version")
    display_name: str = Field(alias="plugin_config.plugin.display_name")
    plugin_pid: uuid.UUID = Field(alias="plugin_config.plugin.pid")
    plugin_config: PluginConfigOutSchema | None = Field(default=None, alias="plugin_config")
    evaluation_inputs: list[EvaluationInputOutSchema] = Field(default=[], alias="evaluation_inputs")

    class Meta:
        model = EvaluationPlugin
        fields = "__all__"
        fields_optional = "__all__"


class ShownEvaluationPluginOutSchema(EvaluationPluginOutSchema):
    """An evaluation plugin as a public route answers it: its configuration masked."""
    plugin_config: ShownPluginConfigOutSchema | None = Field(default=None, alias="plugin_config")
