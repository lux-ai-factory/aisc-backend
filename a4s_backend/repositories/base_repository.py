"""
This class provides some abstractions for common repository logic.
Can be inherited and methods overridden to provide custom logic for specific Models.

Errors are handled and propagated in a generic way to the API with utils/exceptions.py
"""

import uuid
import logging
from typing import TypeVar, Generic, Type, Any

from django.db import models

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=models.Model)


class BaseRepository(Generic[T]):
    model: Type[T]

    def __init__(self, model: Type[T]):
        self.model = model


    async def get(self, pid: uuid.UUID, get_related: bool = False) -> T:
        if get_related:
            return await self.get_with_related(pid)
        return await self.model.objects.aget(pid=pid)


    async def save(self, instance: T) -> T:
        if isinstance(instance, self.model):
            await instance.asave()
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
            await instance.asave()
        elif hasattr(data, '_meta'):
            instance = data
            await instance.asave()
        else:
            instance = self.model(**data)
            await instance.asave()
        return instance


    async def delete(self, instance: T) -> None:
        if isinstance(instance, self.model):
            await instance.adelete()


    async def patch(self, instance: T, data: Any) -> T:
        updated_fields = data.dict(exclude_unset=True)

        for attr, value in updated_fields.items():
            setattr(instance, attr, value)

        return await self.save(instance)
