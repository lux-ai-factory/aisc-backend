"""
This class provides some abstractions for common repository logic.
Can be inherited and methods overridden to provide custom logic for specific Models.

Errors are handled and propagated in a generic way to the API with utils/exceptions.py
"""

import uuid
import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.db import models, router, transaction

logger = logging.getLogger(__name__)


@sync_to_async
def _save_in_one_transaction(instance: models.Model) -> None:
    """Save the row in a transaction of its own, so the ledger event its signal queues
    (aisc_backend.signals.ledger) commits with it, or neither does."""
    with transaction.atomic(using=router.db_for_write(type(instance), instance=instance)):
        instance.save()

class BaseRepository[T: models.Model]:
    model: type[T]

    def __init__(self, model: type[T]):
        self.model = model


    async def get(self, pid: uuid.UUID, get_related: bool = False) -> T:
        if get_related:
            return await self.get_with_related(pid)
        return await self.model.objects.aget(pid=pid)


    async def save(self, instance: T) -> T:
        if isinstance(instance, self.model):
            await _save_in_one_transaction(instance)
        return instance


    async def get_with_related(self, pid: uuid.UUID) -> T:
        raise NotImplementedError("get_with_related() not implemented")


    async def get_including(self, pid: uuid.UUID, include: str)-> T:
        raise NotImplementedError("get_including() not implemented")


    async def filter_with_related(self, **filters: Any) -> list[T]:
        raise NotImplementedError("filter_with_related() not implemented")


    async def get_all(self) -> list[T]:
        return [o async for o in self.model.objects.all()]


    async def get_one(self, **filters: Any) -> T:
        return await self.model.objects.aget(**filters)


    async def filter(self, **filters: Any) -> list[T]:
        return [obj async for obj in self.model.objects.filter(**filters)]


    async def create(self, data: Any) -> T:
        if hasattr(data, 'model_dump'):
            data = data.model_dump()
            instance = self.model(**data)
            await _save_in_one_transaction(instance)
        elif hasattr(data, '_meta'):
            instance = data
            await _save_in_one_transaction(instance)
        else:
            instance = self.model(**data)
            await _save_in_one_transaction(instance)
        return instance


    async def delete(self, instance: T) -> None:
        if isinstance(instance, self.model):
            await instance.adelete()


    async def patch(self, instance: T, data: Any) -> T:
        updated_fields = data.dict(exclude_unset=True)

        for attr, value in updated_fields.items():
            setattr(instance, attr, value)

        return await self.save(instance)
