import uuid

from ninja.testing import TestAsyncClient

from vera_backend.models import FeatureType
from vera_backend.routers.project import router, CreateProjectModelRequest
from vera_backend.schemas.dataset import DatasetOutSchema, DatasetInSchema
from vera_backend.schemas.datashape import DataShapeInSchema, DataShapeOutSchema
from vera_backend.schemas.feature import FeatureInSchema
from vera_backend.schemas.model import ModelOutSchema
from vera_backend.schemas.project import ProjectOutSchema, ProjectInSchema, ProjectDetailsOutSchema

client = TestAsyncClient(router)


async def create_project(name: str) -> ProjectOutSchema:
    data = ProjectInSchema(name=name)
    response = await client.post('', json=data.dict())
    project = ProjectOutSchema.model_construct(**response.data)
    return project


async def get_projects() -> list[ProjectOutSchema]:
    response = await client.get('')
    projects = [ProjectOutSchema.model_construct(**item) for item in response.data]
    return projects


async def patch_project(pid: uuid.UUID, name:str, frequency: str, window_size: str) -> ProjectOutSchema:
    data = ProjectInSchema(name=name, frequency=frequency, window_size=window_size)
    response = await client.patch(f'/{pid}', json=data.dict())
    project = ProjectOutSchema.model_construct(**response.data)
    return project


async def create_project_dataset(pid: uuid.UUID, dataset_name: str) -> DatasetOutSchema:
    data = DatasetInSchema(name=dataset_name)
    response = await client.post(f'/{pid}/datasets', json=data.dict())
    dataset = DatasetOutSchema.model_construct(**response.data)
    return dataset


async def create_project_model(pid: uuid.UUID, model_name: str, dataset_pid: uuid.UUID) -> ModelOutSchema:
    data = CreateProjectModelRequest(name=model_name, dataset_pid=dataset_pid)
    response = await client.post(f'/{pid}/models', json=data.dict())
    model = ModelOutSchema.model_construct(**response.data)
    return model


async def patch_project_datashape(pid: uuid.UUID, datashape: DataShapeOutSchema) -> DataShapeOutSchema:
    features_in: list[FeatureInSchema] = []
    for f in datashape.features:
        feature = FeatureInSchema(name=f['name'], min_value=f['min_value'], max_value=f['max_value'],
                                  feature_type=f['feature_type'])
        features_in.append(feature)

    date_feature = next(f for f in features_in if f.feature_type == FeatureType.Date)
    target_feature = next(f for f in features_in if f.feature_type == FeatureType.Integer and f.max_value == 1)

    features_in = [f for f in features_in if f not in (date_feature, target_feature)]

    data = DataShapeInSchema(features=features_in, date=date_feature, target=target_feature)
    response = await client.patch(f'/{pid}/datashape', json=data.dict())
    datashape = DataShapeOutSchema.model_construct(**response.data)
    return datashape


async def get_project_datashape(pid: uuid.UUID) -> DataShapeOutSchema:
    response = await client.get(f'/{pid}/datashape')
    datashape = DataShapeOutSchema.model_construct(**response.data)
    return datashape

async def get_project_details(pid: uuid.UUID) -> ProjectDetailsOutSchema:
    response = await client.get(f'/{pid}')
    project = ProjectDetailsOutSchema.model_construct(**response.data)
    return project


async def get_project_by_name(name: str) -> ProjectOutSchema:
    response = await client.get(f'/by-name/{name}')
    project = ProjectOutSchema.model_construct(**response.data)
    return project
