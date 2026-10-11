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
    adapters: list[dict] = Field(default=[])

    @staticmethod
    def _adapter_to_dict(adapter):
        return {
            "adapter_class": adapter.adapter_class,
            "package_name": adapter.package_name,
            "version": adapter.version,
        }

    @staticmethod
    def _ordered_adapters(obj):
        cache = getattr(obj, "_prefetched_objects_cache", {}) or {}
        if "adapter_links" in cache:
            links = list(cache["adapter_links"])
            return [link.adapter for link in sorted(links, key=lambda l: l.order)]
        if "adapters" in cache:
            return list(cache["adapters"])
        if hasattr(obj, "adapter_links"):
            try:
                links = list(obj.adapter_links.all())
                if links:
                    return [link.adapter for link in sorted(links, key=lambda l: l.order)]
            except Exception:
                pass
        if hasattr(obj, "adapters") and hasattr(obj.adapters, "all"):
            try:
                return list(obj.adapters.all())
            except Exception:
                return []
        return []

    @staticmethod
    def resolve_adapters(obj):
        return [EvaluationInputOutSchema._adapter_to_dict(a) for a in EvaluationInputOutSchema._ordered_adapters(obj)]

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


class PluginOutSchema(ModelSchema):
    config: dict | None = Field(None, alias="current_config.config")
    pid: uuid.UUID

    class Meta:
        model = Plugin
        fields = ["pid", "name", "package_name", "version", "display_name", "enabled"]


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
