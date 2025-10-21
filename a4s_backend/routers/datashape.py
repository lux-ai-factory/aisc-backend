import uuid

from ninja import Router
from ninja.errors import HttpError

from a4s_backend.models.datashape import DataShape
from a4s_backend.schemas.datashape import DataShapeOutScheme

router = Router(tags=["datashape"])


@router.get("/{datashape_pid}", response=DataShapeOutScheme)
async def get_dataset_datashape(request, datashape_pid: uuid.UUID):
    datashape = await (
        DataShape.objects
        .select_related("dataset")
        .prefetch_related("features")
        .aget(pid=datashape_pid)
    )

    if not datashape:
        raise HttpError(404, f"DataShape ({datashape_pid}) not found")

    return datashape


@router.patch("/{datashape_pid}/status", response=str)
async def update_status(request, datashape_pid: uuid.UUID, status: str):
    datashape = await DataShape.objects.aget(pid=datashape_pid)

    if not datashape:
        raise HttpError(404, f"DataShape ({datashape_pid}) not found")

    datashape.status = status
    await datashape.asave()

    return datashape.status


@router.get("", response=list[DataShapeOutScheme])
async def get_datashapes(request):
    datashapes = [d async for d in DataShape.objects
    .select_related("dataset")
    .prefetch_related("features")
    .all()]
    return datashapes