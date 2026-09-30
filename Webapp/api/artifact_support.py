"""Shared helpers for reading immutable web-run artifacts."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from flask import jsonify


def successful_run(repository, run_id: str | None = None):
    record = repository.get(run_id) if run_id else repository.latest("success")
    if record is None or record.get("status") != "success":
        return None, (jsonify({"error": "No completed run is available."}), 404)
    return record, None


def artifact(repository, record: dict, name: str):
    path = repository.artifact(record["run_id"], name)
    if not path.exists():
        return None, (
            jsonify({"error": f"Artifact {name!r} is not available for this run."}),
            404,
        )
    return path, None


def safe_value(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if hasattr(value, "item"):
        return safe_value(value.item())
    return value


def records(frame: pd.DataFrame) -> list[dict]:
    output = frame.copy()
    for column in output.columns:
        if pd.api.types.is_datetime64_any_dtype(output[column]):
            output[column] = output[column].apply(
                lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else None
            )
    return [
        {key: safe_value(value) for key, value in row.items()}
        for row in output.to_dict(orient="records")
    ]
