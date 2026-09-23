import re

from aisc_backend.models import ProjectConfig, ProjectConfigCategory


def normalize_project_config_key(name: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower())
    key = re.sub(r"_+", "_", key).strip("_")
    if not key:
        raise ValueError("setting name must contain at least one letter or number")
    if key[0].isdigit():
        key = f"setting_{key}"
    return key


async def create_project_config_key(project, category: ProjectConfigCategory, name: str, exclude_pid=None) -> str:
    key = normalize_project_config_key(name)
    queryset = ProjectConfig.objects.filter(project=project, category=category, key=key)
    if exclude_pid is not None:
        queryset = queryset.exclude(pid=exclude_pid)
    if await queryset.aexists():
        raise ValueError(f"A {category.label} named '{name}' already exists in this project")
    return key
