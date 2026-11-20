from typing import Any, List, Dict

from django.db.models import Func, F
from django.db.models import Count, Min, Max, Avg, QuerySet
from django.db import connection

from aisc_evaluation.models import Measurement
from aisc_backend.repositories.base_repository import BaseRepository


class MeasurementRepository(BaseRepository[Measurement]):

    def __init__(self):
        super().__init__(Measurement)

    async def filter_queryset(self, **filters: Any) -> QuerySet[Measurement, Measurement]:
        return self.model.objects.filter(**filters)

    async def get_unique_dimension_keys(self, base_queryset) -> list[str]:
        # Exclude measurements where dimensions is null
        base_queryset = base_queryset.exclude(dimensions__isnull=True)
        if connection.vendor == 'postgresql':
            queryset = base_queryset.annotate(
                key=Func(F('dimensions'), function='jsonb_object_keys')
            ).values_list('key', flat=True).distinct()
            return [key async for key in queryset]
        else:
            # Fallback for SQLite/others: fetch and process in Python
            # WARNING: This can be slow on very large querysets - Should only be used in local development
            unique_keys = set()
            async for dimensions in base_queryset.values_list('dimensions', flat=True):
                if dimensions:
                    unique_keys.update(dimensions.keys())
            return sorted(list(unique_keys))

    async def get_unique_dimension_values(self, base_queryset, key: str) -> list[Any]:
        dimension_field = f"dimensions__{key}"
        queryset = base_queryset.filter(**{f"{dimension_field}__isnull": False})
        values = queryset.values_list(dimension_field, flat=True).distinct()

        return [val async for val in values]

    async def get_unique_metric_names(self, base_queryset) -> list[str]:
        queryset = base_queryset.values_list('metric__name', flat=True).distinct()
        return [name async for name in queryset]

    async def aggregate_measurements(
            self,
            base_queryset,
            group_by: List[str] = None,
            filters: Dict[str, Any] = None,
            aggregations: List[str] = None
    ) -> List[Dict[str, Any]]:
        if aggregations is None:
            aggregations = ["count", "min_score", "max_score", "avg_score"]

        queryset = base_queryset

        if filters:
            prefixed_filters = {
                f"dimensions__{key}": value
                for key, value in filters.items()
            }
            queryset = queryset.filter(**prefixed_filters)

        if group_by:
            values_args = [f"dimensions__{field}" for field in group_by]
            queryset = queryset.values(*values_args)
        else:
            queryset = queryset.values('observation_id')

        agg_map = {}
        if 'count' in aggregations:
            agg_map['count'] = Count('id')
        if 'min_score' in aggregations:
            agg_map['min_score'] = Min('score')
        if 'max_score' in aggregations:
            agg_map['max_score'] = Max('score')
        if 'avg_score' in aggregations:
            agg_map['avg_score'] = Avg('score')

        if agg_map:
            queryset = queryset.annotate(**agg_map)

        return [item async for item in queryset]

    async def metric_summary(self, base_queryset) -> List[Dict[str, Any]]:
        """One row per metric: how many measurements, and their mean/least/most score.

        The chart-shaped view of the same rows `aggregate_measurements` groups by
        observation or dimension — used by the scoped results page, so its charts
        and the visualizer's aggregates are two readings of one query, not two
        code paths.
        """
        rows = (
            base_queryset.values("metric__name", "unit")
            .annotate(
                count=Count("id"),
                min_score=Min("score"),
                max_score=Max("score"),
                avg_score=Avg("score"),
            )
            .order_by("metric__name")
        )
        return [
            {
                "metric": row["metric__name"],
                "unit": row["unit"] or "",
                "count": row["count"],
                "min_score": row["min_score"],
                "max_score": row["max_score"],
                "avg_score": row["avg_score"],
            }
            async for row in rows
        ]

    async def bulk_create(self, instances: list[Measurement]) -> list[Measurement]:
        return await Measurement.objects.abulk_create(instances)