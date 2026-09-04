"""Refresh every local input required by the monthly LDI engine."""

import logging

import pandas as pd

from .bond_cash_flow_creator import build_cashflow_outputs
from .inflation_scenarios import main as build_inflation_scenarios
from scripts.cleaners import bond_cleaner
from scripts.downloaders.bond_downloader import BondDownloader
from scripts.downloaders.download_foi_xt_it import main as download_foi
from scripts.downloaders.download_hicp_xt_ea import main as download_hicp
from scripts.downloaders.yield_curve_downloader import ECBDownloader
from .utils import (
    BOND_CASHFLOWS_PATH,
    BOND_CASHFLOW_MATRIX_PATH,
    CURVE_PATH,
    INFLATION_SCENARIOS_PATH,
    PROJECT_ROOT,
)


CASHFLOWS_PATH = BOND_CASHFLOWS_PATH


def _missing(paths):
    return [path for path in paths if not path.exists()]


def _require_outputs(paths, stage):
    missing = _missing(paths)
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise RuntimeError(f"Pipeline stage {stage} did not produce: {names}")


def _usable_cached_monthly_index(path, value_column, max_age_months=2):
    """Accept an existing official index only when it is recent and well formed."""
    if not path.exists():
        return False
    try:
        cached = pd.read_parquet(path, columns=["date", value_column])
        dates = pd.to_datetime(cached["date"])
        values = pd.to_numeric(cached[value_column], errors="coerce")
    except (OSError, ValueError, KeyError):
        return False
    if (
        cached.empty
        or dates.isna().any()
        or values.isna().any()
        or not (values > 0).all()
        or dates.duplicated().any()
        or not dates.dt.is_month_start.all()
    ):
        return False
    dates = dates.sort_values()
    expected = pd.date_range(dates.iloc[0], dates.iloc[-1], freq="MS")
    if not pd.DatetimeIndex(dates).equals(expected):
        return False
    latest = dates.iloc[-1].to_period("M")
    minimum = pd.Timestamp.today().to_period("M") - max_age_months
    return latest >= minimum


def refresh_ldi_inputs():
    """Download fresh market data and rebuild every derived LDI input."""
    completed = []
    raw_bonds = [bond_cleaner.FD_INPUT, bond_cleaner.BI_INPUT]
    clean_bonds = [bond_cleaner.FD_OUTPUT, bond_cleaner.BI_OUTPUT]

    BondDownloader().run()
    _require_outputs(raw_bonds, "bond download")
    completed.append("bond data")

    ECBDownloader().run()
    _require_outputs([CURVE_PATH], "yield-curve download")
    completed.append("yield curve")

    bond_cleaner.run()
    _require_outputs(clean_bonds, "bond cleaning")
    completed.append("investable universe")

    foi_path = PROJECT_ROOT / "data" / "foi_xt_it.parquet"
    hicp_path = PROJECT_ROOT / "data" / "hicp_xt_ea.parquet"
    if _usable_cached_monthly_index(foi_path, "foi_xt_it"):
        logging.info("FOI cache is current; download skipped.")
    else:
        download_foi()
    if _usable_cached_monthly_index(hicp_path, "hicp_xt_ea"):
        logging.info("HICP cache is current; download skipped.")
    else:
        download_hicp()
    _require_outputs(
        [foi_path, hicp_path],
        "inflation-index download",
    )
    completed.append("inflation indices")

    build_inflation_scenarios()
    _require_outputs([INFLATION_SCENARIOS_PATH], "inflation-scenario generation")
    completed.append("inflation scenarios")

    build_cashflow_outputs()
    _require_outputs(
        [CASHFLOWS_PATH, BOND_CASHFLOW_MATRIX_PATH],
        "bond cash-flow generation",
    )
    completed.append("bond cash flows")

    return completed


def ensure_ldi_inputs():
    """Backward-compatible name for the full input refresh."""
    return refresh_ldi_inputs()
