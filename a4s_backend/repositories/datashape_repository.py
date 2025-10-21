import asyncio
import uuid

from a4s_backend.models import DataShape, Feature
from a4s_backend.schemas.datashape import DataShapeInScheme
from a4s_backend.schemas.feature import FeatureInScheme


async def get_datashape(pid: uuid.UUID) -> DataShape:
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


async def save_datashape(datashape: DataShape, data: DataShapeInScheme) -> DataShape:
    # remove the old features linked to this datashape
    await datashape.features.all().adelete()

    await asyncio.gather(
        *[feature_in_schema_to_feature(f, datashape) for f in data.features]
    )

    target: Feature = await feature_in_schema_to_feature(data.target, datashape)
    date: Feature = await feature_in_schema_to_feature(data.date, datashape)

    datashape.target_feature = target
    datashape.date_feature = date

    await datashape.asave()
    datashape = await get_datashape(datashape.pid)
    return datashape


async def feature_in_schema_to_feature(feature_schema: FeatureInScheme, datashape: DataShape) -> Feature | None:
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
    await feature.asave()
    return feature
