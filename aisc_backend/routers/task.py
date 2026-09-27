from asgiref.sync import sync_to_async
from aisc_backend.auth import membership
from aisc_backend.auth.keycloak import KeycloakAuth
import uuid

from ninja import Router

from aisc_backend.services import celery_service

# Named here as well as API-wide, so request.auth is the verified claims for
# this router on its own: that is what the membership check reads.
router = Router(tags=["task"], auth=KeycloakAuth())


@router.get("{pid}/status", response=dict)
async def get_evaluation_tasks_status(request, pid: uuid.UUID):
    await sync_to_async(membership.for_task)(request, pid)
    result = await celery_service.get_evaluation_tasks_status(pid)
    return result
