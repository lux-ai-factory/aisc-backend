import uuid

from ninja.testing import TestAsyncClient

from aisc_backend.routers.project import router
from aisc_backend.schemas.ai_system import AIComponentOutSchema, AIComponentInSchema
from aisc_backend.schemas.project import ProjectOutSchema, ProjectInSchema, ProjectDetailsOutSchema

client = TestAsyncClient(router)


async def create_project(name: str) -> ProjectOutSchema:
    data = ProjectInSchema(name=name)
    response = await client.post('', json=data.model_dump())
    project = ProjectOutSchema.model_construct(**response.data)
    return project


async def get_projects() -> list[ProjectOutSchema]:
    response = await client.get('')
    projects = [ProjectOutSchema.model_construct(**item) for item in response.data]
    return projects


async def patch_project(pid: uuid.UUID, name: str) -> ProjectOutSchema:
    data = ProjectInSchema(name=name)
    response = await client.patch(f'/{pid}', json=data.model_dump())
    project = ProjectOutSchema.model_construct(**response.data)
    return project


async def create_project_dataset(pid: uuid.UUID, dataset_name: str) -> AIComponentOutSchema:
    data = AIComponentInSchema(name=dataset_name, component_type='dataset')
    response = await client.post(f'/{pid}/components', json=data.model_dump())
    dataset = AIComponentOutSchema.model_construct(**response.data)
    return dataset


async def create_project_model(pid: uuid.UUID, model_name: str, dataset_pid: uuid.UUID) -> AIComponentOutSchema:
    data = AIComponentInSchema(name=model_name, component_type='model')
    response = await client.post(f'/{pid}/components', json=data.model_dump())
    model = AIComponentOutSchema.model_construct(**response.data)
    return model


async def create_project_datashape(pid: uuid.UUID, source_dataset_pid: uuid.UUID) -> AIComponentOutSchema:
    data = AIComponentInSchema(
        name="datashape",
        component_type="datashape",
        source_dataset_pid=source_dataset_pid,
    )
    response = await client.post(f'/{pid}/components', json=data.model_dump())
    datashape = AIComponentOutSchema.model_construct(**response.data)
    return datashape


async def get_project_details(pid: uuid.UUID) -> ProjectDetailsOutSchema:
    response = await client.get(f'/{pid}')
    data = response.data
    components = [AIComponentOutSchema.model_construct(**c) for c in data.get("components", [])]
    plugins = data.get("plugins", [])
    return ProjectDetailsOutSchema.model_construct(
        **{k: v for k, v in data.items() if k not in ("components", "plugins")},
        components=components,
        plugins=plugins,
    )


async def get_project_by_name(name: str) -> ProjectOutSchema:
    response = await client.get(f'/by-name/{name}')
    project = ProjectOutSchema.model_construct(**response.data)
    return project
