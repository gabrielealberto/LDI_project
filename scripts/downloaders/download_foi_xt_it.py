"""Download and rebase ISTAT's monthly Italian FOI excluding tobacco."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from core.ingestion_support import atomic_to_parquet, retry_session

# ISTAT's production SDMX endpoint.  The advertised ``/rest/v2/data`` route
# is not implemented consistently, while this v1-compatible endpoint is the
# one published by ISTAT for data access.
ISTAT_DATA_ROOT = "https://esploradati.istat.it/SDMXWS/rest/data"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "data" / "foi_xt_it.parquet"
TIMEOUT = 30
RIVALUTA_BASE = "https://rivaluta.istat.it/Rivaluta/TavoleStream.action"
HISTORICAL_START = pd.Timestamp("1992-02-01")
HISTORICAL_END = pd.Timestamp("1995-12-01")
ITALIAN_MONTHS = (
    "Gennaio",
    "Febbraio",
    "Marzo",
    "Aprile",
    "Maggio",
    "Giugno",
    "Luglio",
    "Agosto",
    "Settembre",
    "Ottobre",
    "Novembre",
    "Dicembre",
)
HTTP_SESSION = retry_session()
# Official ISTAT linking coefficients.  They are old-base/new-base, therefore
# index values are divided by their cumulative product to express base 2025=100.
SPECS = (
    ("144_110_DF_DCSP_FOI1_1", "M.IT.4.4.00ST", "1995=100", 1.373 * 1.071 * 1.214),
    ("169_15_DF_DCSP_FOI1B2010_1", "M.IT.11.4.00ST", "2010=100", 1.071 * 1.214),
    ("169_745_DF_DCSP_FOI1B2015_1", "M.IT.55.4.00ST", "2015=100", 1.214),
    ("169_748_DF_DCSP_FOI1B2025_1", "M.IT.101.4.00ST", "2025=100", 1.0),
)


def download_sdmx_data(flow: str, key: str) -> pd.DataFrame:
    """Download one constrained ISTAT SDMX series in its tabular format.

    ISTAT's current ``jsondata`` response advertises the right time periods but
    sets every observation value to null.  Its official SDMX-CSV response for
    the same query contains the published values, so use that representation.
    """
    try:
        response = HTTP_SESSION.get(
            f"{ISTAT_DATA_ROOT}/{flow}/{key}",
            params={"format": "csvfile"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return pd.read_csv(BytesIO(response.content))
    except requests.RequestException as error:
        raise RuntimeError(f"ISTAT download failed for {flow}: {error}") from error
    except ValueError as error:
        raise RuntimeError(f"ISTAT returned invalid SDMX-CSV for {flow}") from error


def download_pre_1996() -> pd.DataFrame:
    """Extract Feb-1992--Dec-1995 from ISTAT's official FOI(nt) workbook."""
    try:
        latest = pd.Timestamp.today().to_period("M") - 1
        response = HTTP_SESSION.get(
            RIVALUTA_BASE,
            params={
                "meseA": ITALIAN_MONTHS[latest.month - 1],
                "annoA": latest.year,
                "tav": 7,
                "ultimoAnno": latest.year,
                "ultimoMese": ITALIAN_MONTHS[latest.month - 1],
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        raw = pd.read_excel(BytesIO(response.content), header=None)
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError(
            "ISTAT FOI(nt) historical workbook is unavailable or invalid."
        ) from error
    marker = raw.index[
        raw.iloc[:, 0].astype(str).str.contains("Base 1992=100", na=False)
    ]
    if len(marker) != 1:
        raise RuntimeError(
            "ISTAT workbook does not expose the expected 1992-base FOI segment."
        )
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
        raise RuntimeError(
            f"Expected 47 official pre-1996 FOI observations, got {len(rows)}."
        )
    return pd.DataFrame(rows)


def load_cached_pre_1996(output_path: Path = OUTPUT) -> pd.DataFrame | None:
    """Reuse the immutable Rivaluta segment when a complete local copy exists.

    Rivaluta is only used for Feb-1992--Dec-1995; those observations never
    change.  Avoid making the entire monthly refresh depend on its availability
    once that historical segment has already been validated and stored locally.
    """
    try:
        cached = pd.read_parquet(output_path)
        cached["date"] = pd.to_datetime(cached["date"])
        historical = cached.loc[
            cached["date"].between(HISTORICAL_START, HISTORICAL_END),
        ].copy()
    except (OSError, ValueError, KeyError):
        return None
    expected_dates = pd.date_range(HISTORICAL_START, HISTORICAL_END, freq="MS")
    if (
        len(historical) != len(expected_dates)
        or historical["date"].duplicated().any()
        or not pd.DatetimeIndex(historical["date"].sort_values()).equals(expected_dates)
        or "foi_xt_it" not in historical
        or historical["foi_xt_it"].isna().any()
        or not (pd.to_numeric(historical["foi_xt_it"], errors="coerce") > 0).all()
    ):
        return None
    return historical


def parse_observations(
    payload: pd.DataFrame, flow: str, base: str, coefficient: float
) -> pd.DataFrame:
    """Validate and extract observations from one constrained SDMX-CSV series."""
    required = {
        "FREQ",
        "REF_AREA",
        "DATA_TYPE",
        "MEASURE",
        "TIME_PERIOD",
        "OBS_VALUE",
    }
    if not required.issubset(payload.columns):
        missing = ", ".join(sorted(required - set(payload.columns)))
        raise RuntimeError(f"Unexpected SDMX-CSV schema for {flow}; missing {missing}.")
    if payload.empty:
        raise RuntimeError(f"ISTAT returned no rows for {flow}.")
    category_columns = [
        column
        for column in payload.columns
        if "COICOP" in column and not column.startswith("NOTE_")
    ]
    if len(category_columns) != 1:
        raise RuntimeError(f"Unexpected COICOP dimension in {flow}.")
    category_column = category_columns[0]
    try:
        dimensions = payload[
            ["FREQ", "REF_AREA", "DATA_TYPE", "MEASURE", category_column]
        ]
        if any(dimensions[column].nunique(dropna=False) != 1 for column in dimensions):
            raise RuntimeError(f"ISTAT response for {flow} contains multiple series.")
    except KeyError as error:
        raise RuntimeError(f"Unexpected SDMX-CSV schema for {flow}") from error
    expected = {"FREQ": "M", "REF_AREA": "IT", "MEASURE": 4, category_column: "00ST"}
    for column, value in expected.items():
        actual = dimensions.iloc[0][column]
        if str(actual) != str(value):
            raise RuntimeError(f"Unexpected {column} in {flow}: {actual!r}.")
    if pd.isna(dimensions.iloc[0]["DATA_TYPE"]):
        raise RuntimeError(f"ISTAT response for {flow} has no FOI indicator.")
    rows = []
    for observation in payload.itertuples(index=False):
        number = pd.to_numeric(observation.OBS_VALUE, errors="coerce")
        if pd.isna(number):
            continue
        rows.append(
            {
                "date": pd.Timestamp(f"{observation.TIME_PERIOD}-01"),
                "foi_xt_it": float(number) / coefficient,
                "source_dataset": flow,
                "original_base": base,
                "is_rebased": coefficient != 1.0,
                "rebase_factor": 1.0 / coefficient,
                "observation_status": getattr(observation, "OBS_STATUS", None),
            }
        )
    return pd.DataFrame(rows)


def validate_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Reject invalid observations and retain the newest official overlap."""
    if frame.empty:
        raise RuntimeError("ISTAT returned no observations.")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["foi_xt_it"] = pd.to_numeric(frame["foi_xt_it"], errors="coerce").astype(
        "float64"
    )
    if (
        frame[["date", "foi_xt_it"]].isna().any().any()
        or not (frame["foi_xt_it"] > 0).all()
    ):
        raise RuntimeError("FOI has null, invalid, or non-positive observations.")
    if not frame["date"].dt.is_month_start.all():
        raise RuntimeError("FOI dates are not month starts.")
    frame = frame.sort_values(["date", "rebase_factor"]).drop_duplicates(
        "date", keep="last"
    )
    periods = pd.PeriodIndex(frame["date"], freq="M").asi8
    missing = pd.period_range(frame["date"].min(), frame["date"].max(), freq="M").asi8
    absent = sorted(set(missing) - set(periods))
    if absent:
        logging.warning(
            "Missing months: %s", ", ".join(str(pd.Period(x, freq="M")) for x in absent)
        )
    mean_2025 = frame.loc[frame.date.dt.year.eq(2025), "foi_xt_it"].mean()
    if not np.isfinite(mean_2025) or abs(mean_2025 - 100) > 2:
        raise RuntimeError(
            f"2025 average is incompatible with base 2025=100: {mean_2025}"
        )
    return frame.reset_index(drop=True)


def download_foi_series() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    historical = load_cached_pre_1996()
    if historical is None:
        historical = download_pre_1996()
        logging.info(
            "ISTAT_Rivaluta_FOI_nt: downloaded %s historical observations (1992=100)",
            len(historical),
        )
    else:
        logging.info(
            "ISTAT_Rivaluta_FOI_nt: reused %s immutable historical observations from cache",
            len(historical),
        )
    frames = [historical]
    for flow, key, base, coefficient in SPECS:
        frame = parse_observations(
            download_sdmx_data(flow, key), flow, base, coefficient
        )
        logging.info("%s: %s observations (%s)", flow, len(frame), base)
        frames.append(frame)
    result = validate_series(pd.concat(frames, ignore_index=True))
    atomic_to_parquet(result, OUTPUT)
    check = pd.read_parquet(OUTPUT)
    logging.info("FOI series written: %s observations to %s", len(check), OUTPUT)
