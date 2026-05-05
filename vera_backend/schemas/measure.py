from typing import List, Dict, Any

from ninja import ModelSchema, Schema
from pydantic import field_validator

from vera_backend.models import Measurement


class MeasureInSchema(ModelSchema):
    class Meta:
        model = Measurement
        fields = "__all__"
        fields_optional = "__all__"

    @field_validator('dimensions', check_fields=False, mode='before')
    @classmethod
    def check_flat_and_types(cls, v):
        if v is not None:
            if not isinstance(v, dict):
                raise ValueError("Dimensions must be a JSON object (dictionary).")

            for key, value in v.items():
                if isinstance(value, (dict, list)):
                    raise ValueError(f"Field 'dimensions' must be flat. Key '{key}' is nested.")

                if not isinstance(value, (str, int, bool)):
                    raise ValueError(
                        f"Field 'dimensions' only accepts string, int, or bool. "
                        f"Key '{key}' has invalid type {type(value).__name__}."
                    )
        return v


class MeasureOutSchema(ModelSchema):
    class Meta:
        model = Measurement
        fields = "__all__"


class MeasurementAggregationRequest(Schema):
    group_by: List[str] | None = None
    filters: Dict[str, Any] | None = None
    aggregations: List[str] | None = ["count", "min_score", "max_score", "avg_score"]

class MeasurementAggregationResponse(Schema):
    results: List[Dict[str, Any]]

class DimensionKeysResponse(Schema):
    keys: list[str]

class DimensionValuesResponse(Schema):
    key: str
    values: list[Any]