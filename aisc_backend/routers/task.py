import uuid

from asgiref.sync import sync_to_async

from aisc_backend import deployment
from aisc_backend.auth import membership
from aisc_backend.auth.keycloak import router_auth

from ninja import Router

from aisc_backend.services import celery_service

router = Router(tags=["task"], auth=router_auth())


@router.get("{pid}/status", response=dict)
async def get_evaluation_tasks_status(request, pid: uuid.UUID):
    if deployment.is_configurator():
        await sync_to_async(membership.for_task)(request, pid)
    result = await celery_service.get_evaluation_tasks_status(pid)
    return result
