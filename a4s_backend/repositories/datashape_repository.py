import asyncio
import uuid

from a4s_backend.models import DataShape, Feature
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.schemas.datashape import DataShapeInSchema
from a4s_backend.schemas.feature import FeatureInSchema

feature_repository = BaseRepository(model=Feature)


async def feature_in_schema_to_feature(feature_schema: FeatureInSchema | None, datashape: DataShape) -> Feature | None:
    if feature_schema is None:
        return None

    # We should skip any features coming in from the front end which are missing any of the required fields
    if not feature_schema.name or not feature_schema.feature_type:
        return None

    feature = Feature(
        name=feature_schema.name,
        description=feature_schema.description or "",
        feature_type=feature_schema.feature_type,
        min_value=feature_schema.min_value,
        max_value=feature_schema.max_value,
        datashape=datashape
    )
    return await feature_repository.save(feature)

class DataShapeRepository(BaseRepository[DataShape]):

    def __init__(self):
        super().__init__(DataShape)

    async def patch(self, datashape: DataShape, data: DataShapeInSchema) -> DataShape:
        # remove the old features linked to this datashape
        await datashape.features.all().adelete()

        await asyncio.gather(
            *[feature_in_schema_to_feature(f, datashape) for f in data.features]
        )

        target = await feature_in_schema_to_feature(data.target, datashape)
        date = await feature_in_schema_to_feature(data.date, datashape)

        datashape.target_feature = target
        datashape.date_feature = date

        await self.save(datashape)
        return await self.get(datashape.pid, True)


    async def get_with_related(self, pid: uuid.UUID) -> DataShape:
        datashape = await (
            DataShape.objects
            .select_related("dataset")
            .prefetch_related(
                "features",
                "date_feature",
                "target_feature"
            )
            .aget(pid=pid)
        )

        return datashape
