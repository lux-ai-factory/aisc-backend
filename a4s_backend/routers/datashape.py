import uuid

from ninja import Router

from a4s_backend.repositories.datashape_repository import DataShapeRepository
from a4s_backend.schemas.datashape import DataShapeOutScheme


router = Router(tags=["datashape"])

datashape_repository = DataShapeRepository()


@router.get("/{datashape_pid}", response=DataShapeOutScheme)
async def get_datashape(request, datashape_pid: uuid.UUID):
    return await datashape_repository.get(datashape_pid, True)


@router.patch("/{datashape_pid}/status", response=str)
async def update_datashape_status(request, datashape_pid: uuid.UUID, status: str):
    datashape = await datashape_repository.get(datashape_pid)

    datashape.status = status
    await datashape_repository.save(datashape)

    return datashape.status
