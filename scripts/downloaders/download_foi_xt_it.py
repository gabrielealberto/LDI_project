"""Download and rebase ISTAT's monthly Italian FOI excluding tobacco."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

# ISTAT's production SDMX endpoint.  The advertised ``/rest/v2/data`` route
# is not implemented consistently, while this v1-compatible endpoint is the
# one published by ISTAT for data access.
ISTAT_DATA_ROOT = "https://esploradati.istat.it/SDMXWS/rest/data"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "data" / "foi_xt_it.parquet"
TIMEOUT = 30
RIVALUTA_TABLE = (
    "https://rivaluta.istat.it/Rivaluta/TavoleStream.action?"
    "meseA=Luglio&annoA=2026&tav=7&ultimoAnno=2026&ultimoMese=Luglio"
)
# Official ISTAT linking coefficients.  They are old-base/new-base, therefore
# index values are divided by their cumulative product to express base 2025=100.
SPECS = (
    ("144_110_DF_DCSP_FOI1_1", "M.IT.4.4.00ST", "1995=100", 1.373 * 1.071 * 1.214),
    ("169_15_DF_DCSP_FOI1B2010_1", "M.IT.11.4.00ST", "2010=100", 1.071 * 1.214),
    ("169_745_DF_DCSP_FOI1B2015_1", "M.IT.55.4.00ST", "2015=100", 1.214),
    ("169_748_DF_DCSP_FOI1B2025_1", "M.IT.101.4.00ST", "2025=100", 1.0),
)


def download_sdmx_data(flow: str, key: str) -> dict[str, Any]:
    """Download a single, fully constrained official ISTAT SDMX series."""
    try:
        response = requests.get(
            f"{ISTAT_DATA_ROOT}/{flow}/{key}",
            params={"format": "jsondata"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        raise RuntimeError(f"ISTAT download failed for {flow}: {error}") from error
    except ValueError as error:
        raise RuntimeError(f"ISTAT returned invalid SDMX-JSON for {flow}") from error


def download_pre_1996() -> pd.DataFrame:
    """Extract Feb-1992--Dec-1995 from ISTAT's official FOI(nt) workbook."""
    try:
        response = requests.get(RIVALUTA_TABLE, timeout=TIMEOUT)
        response.raise_for_status()
        raw = pd.read_excel(BytesIO(response.content), header=None)
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError(
            "ISTAT FOI(nt) historical workbook is unavailable or invalid."
        ) from error
    marker = raw.index[raw.iloc[:, 0].astype(str).str.contains("Base 1992=100", na=False)]
    if len(marker) != 1:
        raise RuntimeError("ISTAT workbook does not expose the expected 1992-base FOI segment.")
    factor = 1 / (1.141 * 1.373 * 1.071 * 1.214)
    rows = []
    row_1992 = raw.iloc[marker[0] - 1]
    if pd.to_numeric(row_1992.iloc[0], errors="coerce") != 1992:
        raise RuntimeError("ISTAT workbook does not expose the expected 1992 FOI row.")
    factor_1992 = 1 / (1.189 * 1.141 * 1.373 * 1.071 * 1.214)
    for month, value in enumerate(row_1992.iloc[1:13], start=1):
        if month > 1 and pd.notna(value):
            rows.append(
                {
                    "date": pd.Timestamp(1992, month, 1),
                    "foi_xt_it": float(value) * factor_1992,
                    "source_dataset": "ISTAT_Rivaluta_FOI_nt",
                    "original_base": "1989=100",
                    "is_rebased": True,
                    "rebase_factor": factor_1992,
                    "observation_status": None,
                }
            )
    for _, row in raw.iloc[marker[0] + 2 :].iterrows():
        year = pd.to_numeric(row.iloc[0], errors="coerce")
        if pd.isna(year) or not 1992 <= year <= 1995:
            continue
        for month, value in enumerate(row.iloc[1:13], start=1):
            if year == 1992 and month == 1:
                continue
            if pd.notna(value):
                rows.append(
                    {
                        "date": pd.Timestamp(int(year), month, 1),
                        "foi_xt_it": float(value) * factor,
                        "source_dataset": "ISTAT_Rivaluta_FOI_nt",
                        "original_base": "1992=100",
                        "is_rebased": True,
                        "rebase_factor": factor,
                        "observation_status": None,
                    }
                )
    if len(rows) != 47:
        raise RuntimeError(f"Expected 47 official pre-1996 FOI observations, got {len(rows)}.")
    return pd.DataFrame(rows)


def parse_observations(
    payload: dict[str, Any], flow: str, base: str, coefficient: float
) -> pd.DataFrame:
    """Validate SDMX labels and extract observations from one constrained series."""
    try:
        structure = payload["data"]["structures"][0]
        series_dimensions = structure["dimensions"]["series"]
        observation_dimension = structure["dimensions"]["observation"][0]
        series = payload["data"]["dataSets"][0]["series"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"Unexpected SDMX schema for {flow}") from error
    expected = {
        "FREQ": "M",
        "REF_AREA": "IT",
        "MEASURE": "4",
    }
    labels: dict[str, str] = {}
    for dimension in series_dimensions:
        values = dimension.get("values", [])
        if len(values) != 1:
            raise RuntimeError(f"ISTAT response for {flow} contains multiple series.")
        labels[dimension["id"]] = values[0].get("name", "")
        if dimension["id"] in expected and values[0].get("id") != expected[dimension["id"]]:
            raise RuntimeError(f"Unexpected {dimension['id']} in {flow}.")
    indicator = labels.get("DATA_TYPE", "").lower()
    if "famiglie di operai" not in indicator and "blue and white-collar" not in indicator:
        raise RuntimeError(f"{flow} is not the FOI index.")
    category = next(
        (value for d in series_dimensions if "COICOP" in d["id"] for value in d["values"]), {}
    )
    category_name = category.get("name", "").lower()
    if category.get("id") != "00ST" or not ("tabac" in category_name or "tobacco" in category_name):
        raise RuntimeError(f"{flow} is not the all-items excluding-tobacco series.")
    if len(series) != 1:
        raise RuntimeError(f"ISTAT returned more than one observation series for {flow}.")
    periods = observation_dimension.get("values", [])
    observations = next(iter(series.values())).get("observations", {})
    rows = []
    for position, value in observations.items():
        number = value[0] if isinstance(value, list) else value
        if number is None:
            continue
        period = periods[int(position)]["id"]
        rows.append(
            {
                "date": pd.Timestamp(f"{period}-01"),
                "foi_xt_it": float(number) / coefficient,
                "source_dataset": flow,
                "original_base": base,
                "is_rebased": coefficient != 1.0,
                "rebase_factor": 1.0 / coefficient,
                "observation_status": None,
            }
        )
    return pd.DataFrame(rows)


def validate_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Reject invalid observations and retain the newest official overlap."""
    if frame.empty:
        raise RuntimeError("ISTAT returned no observations.")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["foi_xt_it"] = pd.to_numeric(frame["foi_xt_it"], errors="coerce").astype("float64")
    if frame[["date", "foi_xt_it"]].isna().any().any() or not (frame["foi_xt_it"] > 0).all():
        raise RuntimeError("FOI has null, invalid, or non-positive observations.")
    if not frame["date"].dt.is_month_start.all():
        raise RuntimeError("FOI dates are not month starts.")
    frame = frame.sort_values(["date", "rebase_factor"]).drop_duplicates("date", keep="last")
    periods = pd.PeriodIndex(frame["date"], freq="M").asi8
    missing = pd.period_range(frame["date"].min(), frame["date"].max(), freq="M").asi8
    absent = sorted(set(missing) - set(periods))
    if absent:
        logging.warning(
            "Missing months: %s", ", ".join(str(pd.Period(x, freq="M")) for x in absent)
        )
    mean_2025 = frame.loc[frame.date.dt.year.eq(2025), "foi_xt_it"].mean()
    if not np.isfinite(mean_2025) or abs(mean_2025 - 100) > 2:
        raise RuntimeError(f"2025 average is incompatible with base 2025=100: {mean_2025}")
    return frame.reset_index(drop=True)


def download_foi_series() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    frames = [download_pre_1996()]
    logging.info("ISTAT_Rivaluta_FOI_nt: %s observations (1992=100)", len(frames[0]))
    for flow, key, base, coefficient in SPECS:
        frame = parse_observations(download_sdmx_data(flow, key), flow, base, coefficient)
        logging.info("%s: %s observations (%s)", flow, len(frame), base)
        frames.append(frame)
    result = validate_series(pd.concat(frames, ignore_index=True))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(OUTPUT, index=False, engine="pyarrow")
    check = pd.read_parquet(OUTPUT)
    logging.info("FOI series written: %s observations to %s", len(check), OUTPUT)
