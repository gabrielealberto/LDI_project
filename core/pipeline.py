"""Refresh every local input required by the monthly LDI engine."""

import logging
from datetime import date

import pandas as pd

from .bond_cash_flow_creator import build_cashflow_outputs
from .inflation_baseline import write_baseline as build_inflation_baseline
from scripts.cleaners import bond_cleaner
from scripts.downloaders.bond_downloader import BondDownloader
from scripts.downloaders.download_foi_xt_it import download_foi_series as download_foi
from scripts.downloaders.download_hicp_xt_ea import (
    download_hicp_series as download_hicp,
)
from scripts.downloaders.yield_curve_downloader import ECBDownloader
from .utils import (
    BOND_CASHFLOWS_PATH,
    BOND_CASHFLOW_MATRIX_PATH,
    INFLATION_BASELINE_PATH,
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


def refresh_ldi_inputs(audit=None):
    """Download fresh market data and rebuild every derived LDI input."""
    completed = []
    clean_bonds = [bond_cleaner.FD_OUTPUT, bond_cleaner.BI_OUTPUT]

    bond_snapshot = BondDownloader().run()
    _require_outputs([bond_snapshot.fd_path, bond_snapshot.bi_path], "bond download")
    if bond_snapshot.fallback:
        logging.warning(
            "The investable universe uses a fallback bond snapshot from %s.",
            bond_snapshot.archive_date,
        )
        if audit is not None:
            audit.record_fallback(
                "bond_market",
                archive_date=str(bond_snapshot.archive_date),
                age_days=(date.today() - bond_snapshot.archive_date).days,
            )
    if audit is not None:
        audit.record_input(
            "bond_fd",
            bond_snapshot.fd_path,
            archive_date=str(bond_snapshot.archive_date),
            fallback=bond_snapshot.fallback,
        )
        audit.record_input(
            "bond_bi",
            bond_snapshot.bi_path,
            archive_date=str(bond_snapshot.archive_date),
            fallback=bond_snapshot.fallback,
        )
    completed.append("bond data")

    curve_snapshot = ECBDownloader().run()
    _require_outputs([curve_snapshot.path], "yield-curve download")
    if curve_snapshot.fallback:
        logging.warning(
            "The valuation utilities use a fallback ECB curve snapshot from %s.",
            curve_snapshot.archive_date,
        )
        if audit is not None:
            audit.record_fallback(
                "yield_curve",
                archive_date=str(curve_snapshot.archive_date),
                age_days=(date.today() - curve_snapshot.archive_date).days,
            )
    if audit is not None:
        audit.record_input(
            "yield_curve",
            curve_snapshot.path,
            archive_date=str(curve_snapshot.archive_date),
            fallback=curve_snapshot.fallback,
        )
    completed.append("yield curve")

    bond_cleaner.run(bond_snapshot.fd_path, bond_snapshot.bi_path)
    _require_outputs(clean_bonds, "bond cleaning")
    if audit is not None:
        audit.record_output("fd_clean", bond_cleaner.FD_OUTPUT)
        audit.record_output("bi_clean", bond_cleaner.BI_OUTPUT)
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
    if audit is not None:
        audit.record_input(
            "foi",
            foi_path,
            cache_reused=_usable_cached_monthly_index(foi_path, "foi_xt_it"),
            last_observation=str(
                pd.read_parquet(foi_path, columns=["date"]).date.max()
            ),
        )
        audit.record_input(
            "hicp",
            hicp_path,
            cache_reused=_usable_cached_monthly_index(hicp_path, "hicp_xt_ea"),
            last_observation=str(
                pd.read_parquet(hicp_path, columns=["date"]).date.max()
            ),
        )
    completed.append("inflation indices")

    build_inflation_baseline()
    _require_outputs([INFLATION_BASELINE_PATH], "inflation-baseline generation")
    if audit is not None:
        audit.record_input("inflation_baseline", INFLATION_BASELINE_PATH)
        audit.record_configuration(
            "liabilities", PROJECT_ROOT / "data" / "config" / "liabilities.json"
        )
        audit.record_configuration(
            "inflation_linked_bonds",
            PROJECT_ROOT / "data" / "config" / "inflation_linked_bonds.json",
        )
        audit.record_configuration(
            "inflation_stress_scenarios",
            PROJECT_ROOT / "data" / "config" / "inflation_stress_scenarios.json",
        )
    completed.append("inflation baseline")

    build_cashflow_outputs()
    _require_outputs(
        [CASHFLOWS_PATH, BOND_CASHFLOW_MATRIX_PATH],
        "bond cash-flow generation",
    )
    if audit is not None:
        audit.record_output("bond_cashflows", CASHFLOWS_PATH)
        audit.record_output("bond_cashflow_matrix", BOND_CASHFLOW_MATRIX_PATH)
    completed.append("bond cash flows")

    return completed


def ensure_ldi_inputs():
    """Backward-compatible name for the full input refresh."""
    return refresh_ldi_inputs()
