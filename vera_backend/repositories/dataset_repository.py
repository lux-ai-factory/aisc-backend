import uuid

from vera_backend.models import Dataset
from vera_backend.repositories.base_repository import BaseRepository


class DatasetRepository(BaseRepository[Dataset]):

    def __init__(self):
        super().__init__(Dataset)

    async def get_with_related(self, pid: uuid.UUID) -> Dataset:
        return await (
            Dataset.objects
            .select_related("project")
            .aget(pid=pid)
        )
