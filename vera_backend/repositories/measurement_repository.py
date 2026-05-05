from typing import Any, List, Dict

from django.db.models import Func, F
from django.db.models import Count, Min, Max, Avg, QuerySet
from django.db import connection

from vera_backend.models import Measurement
from vera_backend.repositories.base_repository import BaseRepository


class MeasurementRepository(BaseRepository[Measurement]):

    def __init__(self):
        super().__init__(Measurement)

    async def filter_queryset(self, **filters: Any) -> QuerySet[Measurement, Measurement]:
        return self.model.objects.filter(**filters)

    async def get_unique_dimension_keys(self, base_queryset) -> list[str]:
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

    async def aggregate_measurements(
            self,
            base_queryset,
            group_by: List[str] = None,
            filters: Dict[str, Any] = None,
            aggregations: List[str] = None
    ) -> List[Dict[str, Any]]:
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

        agg_map = {}
        if aggregations:
            for agg in aggregations:
                if agg == 'count':
                    agg_map['count'] = Count('id')
                elif agg == 'min_score':
                    agg_map['min_score'] = Min('score')
                elif agg == 'max_score':
                    agg_map['max_score'] = Max('score')
                elif agg == 'avg_score':
                    agg_map['avg_score'] = Avg('score')

        if agg_map:
            queryset = queryset.annotate(**agg_map)

        return [item async for item in queryset]