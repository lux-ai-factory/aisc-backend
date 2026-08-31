import pandas as pd


def validate_dataframe_against_datashape(df: pd.DataFrame, datashape: dict) -> dict:
    errors, warnings = [], []
    for feature in datashape.get("features", []):
        if feature.get("role") == "ignore":
            continue
        name = feature["name"]
        if name not in df.columns:
            errors.append(f"missing column: {name}")
            continue
        series = df[name]
        expected = feature.get("semantic_type")
        compatible = not (
            expected == "numeric" and not pd.api.types.is_numeric_dtype(series)
            or expected == "boolean" and not pd.api.types.is_bool_dtype(series)
            or expected == "datetime" and not pd.api.types.is_datetime64_any_dtype(series)
        )
        if not compatible:
            errors.append(f"incompatible dtype for {name}: expected {feature.get('dtype', expected)}")
            continue
        if expected == "numeric" and feature.get("min") is not None:
            if series.min() < feature["min"] or series.max() > feature["max"]:
                warnings.append(f"{name}: values outside [{feature['min']}, {feature['max']}]")
        mapping = feature.get("category_mapping") or {}
        if mapping:
            observed = {str(value) for value in series.dropna().unique()}
            known = set(mapping)
            if unseen := observed - known:
                warnings.append(f"{name}: unseen categories {sorted(unseen)!r}")
            if missing := known - observed:
                warnings.append(f"{name}: expected categories never observed {sorted(missing)!r}")
    return {"errors": errors, "warnings": warnings}
