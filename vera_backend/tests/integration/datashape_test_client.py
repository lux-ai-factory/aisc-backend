import uuid

from ninja.testing import TestAsyncClient

from vera_backend.models import DataShapeStatus
from vera_backend.routers.datashape import router
from vera_backend.schemas.datashape import DataShapeOutSchema


client = TestAsyncClient(router)


async def get_datashape(pid: uuid.UUID) -> DataShapeOutSchema:
    response = await client.get(f'/{pid}')
    datashape = DataShapeOutSchema.model_construct(**response.data)
    return datashape


async def patch_datashape_status(pid: uuid.UUID, status: DataShapeStatus) -> DataShapeStatus:
    response = await client.patch(f'/{pid}/status?status={status}')
    return response.data
