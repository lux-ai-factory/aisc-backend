import uuid

from ninja import Router

from a4s_backend.services import celery_service

router = Router(tags=["task"])


@router.get("{pid}/status", response=dict)
async def get_task_status(request, pid: uuid.UUID):
    result = await celery_service.check_task_status(pid)
    return result
