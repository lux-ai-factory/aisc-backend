import uuid

from a4s_plugin_manager import Loader
from ninja import Router, Schema

from a4s_backend.models import Plugin
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.repositories.project_repository import ProjectRepository
from a4s_backend.services import celery_service
from config.settings import APP_NAME, DEV_PLUGIN_PATH

router = Router(tags=["app"])

plugin_loader: Loader = Loader(DEV_PLUGIN_PATH)

plugin_repository = BaseRepository(model=Plugin)
project_repository = ProjectRepository()


@router.get("/app-name", response=str)
async def get_app_name(request):
    return APP_NAME


@router.get("/plugins", response=list[str])
async def get_plugins(request):
    plugins = plugin_loader.list_plugins()
    plugin_names = [p for p in plugins.keys()]
    return plugin_names


@router.get("/plugins/{plugin_name}/config", response=dict)
async def get_plugin_config(request, plugin_name: str):
    plugin = plugin_loader.load(plugin_name)
    return plugin.get_config()


@router.get("/plugins/{pid}/run", response=str)
async def run_plugins(request, pid: uuid.UUID):
    group_task = await celery_service.run_plugins(pid)
    return str(group_task.task_id)

class CreatePluginConfigRequest(Schema):
    name: str
    config: dict
    project_uuid: uuid.UUID

@router.post("/plugins/{plugin_name}", response=str)
async def create_plugin_config(request, data: CreatePluginConfigRequest):
    project = await project_repository.get(data.project_uuid, True)

    plugin = next((p for p in project.get_plugins() if p.name == data.name), None)
    if not plugin:
        plugin = Plugin(name=data.name, config=data.config, project=project)
        plugin = await plugin_repository.create(plugin)

    return plugin.name

@router.get("/plugins/{pid}/status", response=dict)
async def get_plugin_status(request, pid: uuid.UUID):
    result = await celery_service.check_task_status(pid)
    return result