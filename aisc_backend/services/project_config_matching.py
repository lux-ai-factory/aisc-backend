from typing import Any

from aisc_plugin_interface import ConfigCategory, ProjectConfigDefinition

from aisc_backend.models import ProjectConfig

def _compatible(setting: ProjectConfig, definition: ProjectConfigDefinition) -> bool:
    if setting.category != definition.category.value:
        return False
    if definition.category == ConfigCategory.VARIABLES:
        return setting.json_value.get("type") == definition.value_type.value if definition.value_type else True
    return True


async def validate_plugin_settings(
    project,
    plugin_name: str,
    config: dict[str, Any],
    definitions: list[ProjectConfigDefinition],
    selected_settings: list[ProjectConfig] | None = None,
) -> dict[str, list[dict[str, str]]]:
    settings = selected_settings if selected_settings is not None else [
        setting async for setting in ProjectConfig.objects.filter(project=project)
    ]
    missing: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    for definition in definitions:
        if not definition.required:
            continue
        if definition.category.value == "datashape":
            continue
        compatible = [setting for setting in settings if _compatible(setting, definition)]
        referenced_key = definition.key
        if not compatible:
            missing.append({"plugin": plugin_name, "setting": definition.name, "key": definition.key, "reason": "required setting is not configured"})
    return {"missing": missing, "invalid": invalid, "ambiguous": ambiguous}
