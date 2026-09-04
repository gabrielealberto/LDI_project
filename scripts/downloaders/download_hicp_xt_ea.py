"""Download and rebase the Euro area HICP excluding tobacco series."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


API_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "data" / "hicp_xt_ea.parquet"
TIMEOUT_SECONDS = 30
OVERLAP_TOLERANCE = 0.001
MIN_OVERLAP_MONTHS = 3

SERIES = {
    "historical": {
        "dataset": "prc_hicp_midx",
        "unit": "I15",
        "classification_dimension": "coicop",
        "source_dataset": "prc_hicp_midx",
    },
    "current": {
        "dataset": "teicp240",
        "unit": "I25",
        # Eurostat's current ECOICOP v2 endpoint calls this dimension coicop18.
        "classification_dimension": "coicop18",
        "source_dataset": "teicp240",
    },
}


def download_eurostat_series(specification: dict[str, str]) -> dict[str, Any]:
    """Request one precisely filtered JSON-stat dataset from Eurostat."""
    params = {
        "freq": "M",
        "unit": specification["unit"],
        specification["classification_dimension"]: "TOT_X_TBC",
        "geo": "EA",
    }
    url = f"{API_URL}/{specification['dataset']}"
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise RuntimeError(
            f"Eurostat request failed for {specification['dataset']}: {error}"
        ) from error
    except ValueError as error:
        raise RuntimeError(
            f"Eurostat returned invalid JSON for {specification['dataset']}."
        ) from error

    if payload.get("class") != "dataset" or not isinstance(payload.get("dimension"), dict):
        raise RuntimeError(f"Unexpected JSON-stat schema for {specification['dataset']}.")
    return payload


def _category_codes(payload: dict[str, Any], dimension: str) -> set[str]:
    try:
        index = payload["dimension"][dimension]["category"]["index"]
    except KeyError as error:
        raise RuntimeError(f"Eurostat JSON-stat lacks dimension {dimension!r}.") from error
    if not isinstance(index, dict):
        raise RuntimeError(f"Eurostat JSON-stat index for {dimension!r} is invalid.")
    return set(index)


def _validate_metadata(payload: dict[str, Any], specification: dict[str, str]) -> list[str]:
    """Ensure the response contains only the required monthly EA aggregate."""
    classification_dimension = specification["classification_dimension"]
    required = {
        "freq": {"M"},
        "unit": {specification["unit"]},
        classification_dimension: {"TOT_X_TBC"},
        "geo": {"EA"},
    }
    ids = payload.get("id")
    if not isinstance(ids, list) or "time" not in ids:
        raise RuntimeError("Eurostat JSON-stat dimensions are missing or malformed.")
    for dimension, expected in required.items():
        actual = _category_codes(payload, dimension)
        if actual != expected:
            raise RuntimeError(
                f"Unexpected {dimension} selection in {specification['dataset']}: "
                f"expected {sorted(expected)}, got {sorted(actual)}."
            )
    return ids


def parse_eurostat_jsonstat(payload: dict[str, Any], specification: dict[str, str]) -> pd.DataFrame:
    """Convert a one-series Eurostat JSON-stat response into dated observations."""
    dimensions = _validate_metadata(payload, specification)
    time_index = payload["dimension"]["time"]["category"]["index"]
    if not isinstance(time_index, dict):
        raise RuntimeError("Eurostat JSON-stat time index is invalid.")

    values = payload.get("value", {})
    statuses = payload.get("status", {})
    if not isinstance(values, dict) or not isinstance(statuses, dict):
        raise RuntimeError("Eurostat JSON-stat values or status flags are invalid.")

    non_time_size = 1
    for dimension, size in zip(dimensions, payload.get("size", []), strict=True):
        if dimension != "time":
            non_time_size *= int(size)
    if non_time_size != 1:
        raise RuntimeError("Eurostat response contains more than one requested series.")

    rows: list[dict[str, Any]] = []
    for period, position in time_index.items():
        raw_value = values.get(str(position))
        if raw_value is None:
            continue
        try:
            date = pd.Timestamp(f"{period}-01")
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"Invalid Eurostat observation at {period!r}.") from error
        rows.append(
            {
                "date": date,
                "hicp_xt_ea": value,
                "source_dataset": specification["source_dataset"],
                "original_unit": specification["unit"],
                "status_flag": statuses.get(str(position)),
            }
        )
    if not rows:
        raise RuntimeError(f"Eurostat returned no observations for {specification['dataset']}.")
    return pd.DataFrame(rows)


def validate_series(series: pd.DataFrame, name: str) -> pd.DataFrame:
    """Validate dates, values, duplicate observations, and ordering."""
    required_columns = {"date", "hicp_xt_ea"}
    if not required_columns.issubset(series.columns):
        raise ValueError(f"{name} is missing required columns.")
    checked = series.copy()
    checked["date"] = pd.to_datetime(checked["date"], errors="coerce")
    checked["hicp_xt_ea"] = pd.to_numeric(checked["hicp_xt_ea"], errors="coerce")
    if checked[["date", "hicp_xt_ea"]].isna().any().any():
        raise ValueError(f"{name} contains null or invalid downloaded observations.")
    if not checked["date"].dt.is_month_start.all():
        raise ValueError(f"{name} contains dates that are not month starts.")
    if not np.isfinite(checked["hicp_xt_ea"]).all() or not (checked["hicp_xt_ea"] > 0).all():
        raise ValueError(f"{name} contains non-finite or non-positive index values.")

    duplicates = checked[checked.duplicated("date", keep=False)]
    if not duplicates.empty:
        conflicting = duplicates.groupby("date")["hicp_xt_ea"].nunique().gt(1)
        if conflicting.any():
            dates = conflicting[conflicting].index.strftime("%Y-%m-%d").tolist()
            raise ValueError(f"{name} has conflicting duplicate values at {dates}.")
        checked = checked.drop_duplicates("date", keep="first")
    return checked.sort_values("date").reset_index(drop=True)


def calculate_rebasing_factor(
    historical: pd.DataFrame, current: pd.DataFrame
) -> tuple[float, float]:
    """Estimate the I15-to-I25 factor from common 2025 monthly observations."""
    overlap = historical[["date", "hicp_xt_ea"]].merge(
        current[["date", "hicp_xt_ea"]], on="date", suffixes=("_old", "_new")
    )
    preferred = overlap[overlap["date"].dt.year.eq(2025)]
    sample = preferred if len(preferred) >= MIN_OVERLAP_MONTHS else overlap
    if len(sample) < MIN_OVERLAP_MONTHS:
        raise RuntimeError(
            f"Only {len(sample)} overlapping months are available; at least "
            f"{MIN_OVERLAP_MONTHS} are required for rebasing."
        )
    ratios = sample["hicp_xt_ea_new"] / sample["hicp_xt_ea_old"]
    factor = float(ratios.median())
    deviation = float((ratios / factor - 1).abs().max())
    if not np.isfinite(factor) or factor <= 0 or deviation > OVERLAP_TOLERANCE:
        raise RuntimeError(
            "The old and current series cannot be joined by a simple rebasing: "
            f"factor={factor}, maximum deviation={deviation:.6f}."
        )
    return factor, deviation


def merge_series(historical: pd.DataFrame, current: pd.DataFrame, factor: float) -> pd.DataFrame:
    """Rebase legacy observations and prefer the current series on overlap."""
    legacy = historical.copy()
    legacy["hicp_xt_ea"] = (legacy["hicp_xt_ea"] * factor).astype("float64")
    legacy["is_rebased"] = True
    current_rows = current.copy()
    current_rows["hicp_xt_ea"] = current_rows["hicp_xt_ea"].astype("float64")
    current_rows["is_rebased"] = False

    current_start = current_rows["date"].min()
    combined = pd.concat([legacy[legacy["date"] < current_start], current_rows], ignore_index=True)
    combined = validate_series(combined, "merged series")
    if combined["date"].duplicated().any():
        raise ValueError("The merged series contains duplicate months.")
    mean_2025 = combined.loc[combined["date"].dt.year.eq(2025), "hicp_xt_ea"].mean()
    if not np.isfinite(mean_2025) or abs(mean_2025 - 100.0) > 2.0:
        raise RuntimeError(f"The 2025 mean ({mean_2025:.3f}) is not close to base 2025=100.")
    return combined.drop(columns="status_flag")


def missing_months(series: pd.DataFrame) -> pd.DatetimeIndex:
    """Return absent months without inventing values for them."""
    expected = pd.date_range(series["date"].min(), series["date"].max(), freq="MS")
    return expected.difference(pd.DatetimeIndex(series["date"]))


def save_parquet(series: pd.DataFrame, output_path: Path) -> None:
    """Persist the final typed series as a Parquet file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = series[["date", "hicp_xt_ea", "source_dataset", "original_unit", "is_rebased"]].copy()
    output["date"] = pd.to_datetime(output["date"]).astype("datetime64[ns]")
    output["hicp_xt_ea"] = output["hicp_xt_ea"].astype("float64")
    output.to_parquet(output_path, index=False, engine="pyarrow")


def main() -> None:
    """Download, validate, merge, save, and verify the requested series."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    historical = validate_series(
        parse_eurostat_jsonstat(
            download_eurostat_series(SERIES["historical"]), SERIES["historical"]
        ),
        "historical series",
    )
    current = validate_series(
        parse_eurostat_jsonstat(download_eurostat_series(SERIES["current"]), SERIES["current"]),
        "current series",
    )
    factor, deviation = calculate_rebasing_factor(historical, current)
    merged = merge_series(historical, current, factor)
    missing = missing_months(merged)
    if len(missing):
        logging.warning("Missing monthly observations: %s", ", ".join(missing.strftime("%Y-%m-%d")))
    save_parquet(merged, OUTPUT_PATH)

    check = pd.read_parquet(OUTPUT_PATH)
    print("HICP XT EA saved successfully")
    print(f"Start: {check['date'].min():%Y-%m-%d}")
    print(f"End: {check['date'].max():%Y-%m-%d}")
    print(f"Observations: {len(check)}")
    print(f"Missing months: {len(missing)}")
    print(f"Rebasing factor: {factor:.10f}")
    print(f"Maximum overlap deviation: {deviation:.10f}")
    print(f"Latest value: {check['hicp_xt_ea'].iloc[-1]:.2f}")
    print(f"Output: {OUTPUT_PATH}")
    print("\ncheck.head()")
    print(check.head())
    print("\ncheck.tail()")
    print(check.tail())
    print("\ncheck.dtypes")
    print(check.dtypes)


if __name__ == "__main__":
    main()
