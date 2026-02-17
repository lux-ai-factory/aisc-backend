import uuid

from ninja import Router

from a4s_backend.services import celery_service

router = Router(tags=["task"])


@router.get("{pid}/status", response=dict)
async def get_evaluation_tasks_status(request, pid: uuid.UUID):
    result = await celery_service.get_evaluation_tasks_status(pid)
    return result
