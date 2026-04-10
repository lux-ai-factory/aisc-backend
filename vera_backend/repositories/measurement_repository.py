from typing import Any

from vera_backend.models import Measurement
from vera_backend.repositories.base_repository import BaseRepository


class MeasurementRepository(BaseRepository[Measurement]):

    def __init__(self):
        super().__init__(Measurement)

    async def filter_with_related(self, **filters: Any) -> list[Measurement]:
        return [m async for m in Measurement.objects.filter(**filters).select_related("feature").all()]
