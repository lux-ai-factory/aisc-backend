from typing import Any

from aisc_plugin_interface import SettingCategory, SettingDefinition

from aisc_backend.models import ProjectSetting

def _compatible(setting: ProjectSetting, definition: SettingDefinition) -> bool:
    if setting.category != definition.category.value:
        return False
    if definition.category == SettingCategory.GENERAL:
        return setting.json_value.get("type") == definition.value_type.value if definition.value_type else True
    return True


async def validate_plugin_settings(
    project,
    plugin_name: str,
    config: dict[str, Any],
    definitions: list[SettingDefinition],
    selected_settings: list[ProjectSetting] | None = None,
) -> dict[str, list[dict[str, str]]]:
    settings = selected_settings if selected_settings is not None else [
        setting async for setting in ProjectSetting.objects.filter(project=project)
    ]
    missing: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    for definition in definitions:
        if not definition.required:
            continue
        if definition.category == SettingCategory.DATASHAPE:
            continue
        compatible = [setting for setting in settings if _compatible(setting, definition)]
        referenced_key = definition.key
        if not compatible:
            missing.append({"plugin": plugin_name, "setting": definition.name, "key": definition.key, "reason": "required setting is not configured"})
    return {"missing": missing, "invalid": invalid, "ambiguous": ambiguous}
