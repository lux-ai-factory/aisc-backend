import io
from typing import Any

import pandas as pd
from django.utils import timezone

CATEGORY_ABS_CAP = 50
CATEGORY_SMALL = 20
CATEGORY_REL = 0.05


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def derive_features(content: bytes, fmt: str, source_dataset_pid: str) -> dict:
    if fmt == "csv":
        df = pd.read_csv(io.BytesIO(content))
    elif fmt == "parquet":
        df = pd.read_parquet(io.BytesIO(content))
    else:
        raise ValueError(f"unsupported format: {fmt}")

    features = []
    for name in df.columns:
        series = df[name]
        unique_count = int(series.nunique(dropna=True))
        categorical = unique_count <= CATEGORY_SMALL or (
            unique_count <= CATEGORY_ABS_CAP and len(df) > 0 and unique_count / len(df) < CATEGORY_REL
        )
        if pd.api.types.is_bool_dtype(series):
            semantic_type = "boolean"
        elif pd.api.types.is_datetime64_any_dtype(series):
            semantic_type = "datetime"
        elif pd.api.types.is_numeric_dtype(series):
            semantic_type = "categorical" if categorical else "numeric"
        else:
            semantic_type = "categorical" if categorical else "text"
        feature = {
            "name": str(name),
            "dtype": str(series.dtype),
            "semantic_type": semantic_type,
            "role": "feature",
            "null_count": int(series.isna().sum()),
            "unique_count": unique_count,
        }
        if pd.api.types.is_numeric_dtype(series):
            low, high = series.min(), series.max()
            feature["min"] = float(low) if pd.notna(low) else None
            feature["max"] = float(high) if pd.notna(high) else None
            if semantic_type == "numeric":
                mean, std = series.mean(), series.std()
                feature["mean"] = float(mean) if pd.notna(mean) else None
                feature["std"] = float(std) if pd.notna(std) else None
        if unique_count <= CATEGORY_ABS_CAP:
            values = [_json_value(value) for value in series.dropna().unique().tolist()]
            feature["categories"] = values
            if semantic_type == "categorical":
                feature["category_mapping"] = {str(value): str(value) for value in values}
        features.append(feature)
    return {
        "version": 1,
        "source_dataset_pid": source_dataset_pid,
        "source_format": fmt,
        "derived_at": timezone.now().isoformat(),
        "row_count": len(df),
        "features": features,
    }
