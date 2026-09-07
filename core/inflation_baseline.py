"""Build the single, dynamic FOI/HICP baseline used by the LDI workflow.

The forecast deliberately favours transparent, stable behaviour over a
high-parameter statistical model.  It uses only the official monthly index
histories already refreshed by the project: a robust estimate of recent
inflation, a monthly seasonal profile, and gradual convergence to the ECB's
2% medium-term inflation target. FOI remains coherent with HICP through a
temporary, mean-reverting Italy/euro-area inflation spread.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
FOI_PATH = ROOT / "data" / "foi_xt_it.parquet"
HICP_PATH = ROOT / "data" / "hicp_xt_ea.parquet"
LIABILITIES_PATH = ROOT / "data" / "config" / "liabilities.json"
INFLATION_LINKED_PATH = ROOT / "data" / "config" / "inflation_linked_bonds.json"
OUTPUT_PATH = ROOT / "data" / "processed" / "inflation_baseline.parquet"


@dataclass(frozen=True)
class BaselineConfig:
    """Parameters for the deterministic, anchor-and-decay baseline."""

    annual_target: float = 0.02
    convergence_half_life_months: float = 24.0
    spread_half_life_months: float = 30.0
    seasonal_years: int = 10
    long_run_foi_hicp_log_spread: float = 0.0
    model_version: str = "anchor_decay_v1"


def forecast_end_date(
    liabilities_path: Path = LIABILITIES_PATH,
    inflation_linked_path: Path = INFLATION_LINKED_PATH,
) -> pd.Timestamp:
    """Cover all configured liability payments and indexed redemptions."""
    liabilities = json.loads(liabilities_path.read_text(encoding="utf-8"))
    liability_end = pd.Timestamp(
        max(record["end_date"] for record in liabilities)
    ).replace(day=1)
    bonds = json.loads(inflation_linked_path.read_text(encoding="utf-8"))
    latest_maturity = pd.Timestamp(
        max(term["maturity_date"] for term in bonds.values())
    ).replace(day=1)
    return max(liability_end, latest_maturity)


def load_history(
    foi_path: Path = FOI_PATH, hicp_path: Path = HICP_PATH
) -> pd.DataFrame:
    """Load a validated, gap-free overlap of the two official monthly indices."""
    foi = pd.read_parquet(foi_path)[["date", "foi_xt_it"]]
    hicp = pd.read_parquet(hicp_path)[["date", "hicp_xt_ea"]]
    history = (
        foi.merge(hicp, on="date", how="inner")
        .sort_values("date")
        .reset_index(drop=True)
    )
    if len(history) < 120 or history.isna().any().any():
        raise ValueError(
            "At least ten clean years of overlapping FOI/HICP are required."
        )
    if not (history[["foi_xt_it", "hicp_xt_ea"]] > 0).all().all():
        raise ValueError("Inflation index levels must be strictly positive.")
    months = pd.PeriodIndex(history["date"], freq="M").asi8
    if not np.all(np.diff(months) == 1):
        raise ValueError("Overlapping FOI/HICP history must be monthly and gap-free.")
    return history


def _annual_log_inflation(levels: pd.Series) -> pd.Series:
    return np.log(levels / levels.shift(12))


def _recent_annual_rate(annual_log: pd.Series) -> float:
    """Robustly combine the latest one, two and five years of inflation."""
    clean = annual_log.dropna()
    if len(clean) < 60:
        raise ValueError(
            "At least five years of annual inflation observations are required."
        )
    windows = (clean.iloc[-12:], clean.iloc[-24:-12], clean.iloc[-60:-24])
    medians = [float(window.median()) for window in windows]
    return float(np.dot((0.50, 0.30, 0.20), medians))


def _seasonal_log_changes(
    levels: pd.Series, dates: pd.Series, years: int
) -> np.ndarray:
    """Return a zero-sum robust monthly seasonal profile of log changes."""
    changes = np.log(levels / levels.shift(1))
    frame = pd.DataFrame(
        {"month": pd.to_datetime(dates).dt.month, "change": changes}
    ).dropna()
    frame = frame.tail(years * 12)
    if frame.groupby("month").size().lt(1).any():
        raise ValueError(
            "Insufficient history to estimate every monthly seasonal effect."
        )
    seasonal = (
        frame.groupby("month")["change"].median().reindex(range(1, 13)).to_numpy(float)
    )
    return seasonal - seasonal.mean()


def _decay(start: float, anchor: float, horizon: int, half_life_months: float) -> float:
    if half_life_months <= 0:
        raise ValueError("Half-life must be strictly positive.")
    return anchor + (start - anchor) * 0.5 ** (horizon / half_life_months)


def build_baseline(
    history: pd.DataFrame,
    end_date: pd.Timestamp,
    config: BaselineConfig = BaselineConfig(),
) -> pd.DataFrame:
    """Forecast one coherent monthly FOI/HICP baseline through ``end_date``."""
    required = {"date", "foi_xt_it", "hicp_xt_ea"}
    if missing := required - set(history):
        raise ValueError(f"History is missing required columns: {sorted(missing)}")
    history = history[["date", "foi_xt_it", "hicp_xt_ea"]].copy()
    history["date"] = pd.to_datetime(history["date"], errors="coerce")
    if history.empty or history["date"].isna().any():
        raise ValueError("History must contain valid monthly dates.")
    if history["date"].duplicated().any() or not history["date"].dt.is_month_start.all():
        raise ValueError("History must contain unique month-start dates.")
    history[["foi_xt_it", "hicp_xt_ea"]] = history[["foi_xt_it", "hicp_xt_ea"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if history[["foi_xt_it", "hicp_xt_ea"]].isna().any().any() or not np.isfinite(
        history[["foi_xt_it", "hicp_xt_ea"]].to_numpy()
    ).all():
        raise ValueError("History contains invalid inflation index levels.")
    if not (history[["foi_xt_it", "hicp_xt_ea"]] > 0).all().all():
        raise ValueError("History index levels must be strictly positive.")
    history = history.sort_values("date").reset_index(drop=True)
    months = pd.PeriodIndex(history["date"], freq="M").asi8
    if len(months) < 2 or not np.all(np.diff(months) == 1):
        raise ValueError("History must be monthly and gap-free.")
    first = pd.Timestamp(history["date"].iloc[-1]) + pd.offsets.MonthBegin(1)
    dates = pd.date_range(first, pd.Timestamp(end_date), freq="MS")
    if dates.empty:
        raise ValueError(
            "Forecast horizon must extend beyond the latest historical observation."
        )

    foi_annual = _annual_log_inflation(history["foi_xt_it"])
    hicp_annual = _annual_log_inflation(history["hicp_xt_ea"])
    hicp_start = _recent_annual_rate(hicp_annual)
    spread = foi_annual - hicp_annual
    spread_start = _recent_annual_rate(spread)
    hicp_seasonal = _seasonal_log_changes(
        history["hicp_xt_ea"], history["date"], config.seasonal_years
    )
    foi_seasonal = _seasonal_log_changes(
        history["foi_xt_it"], history["date"], config.seasonal_years
    )
    target_log = float(np.log1p(config.annual_target))

    levels = history[["foi_xt_it", "hicp_xt_ea"]].iloc[-1].to_numpy(float)
    rows = []
    for horizon, date in enumerate(dates, start=1):
        hicp_annual_rate = _decay(
            hicp_start, target_log, horizon, config.convergence_half_life_months
        )
        spread_rate = _decay(
            spread_start,
            config.long_run_foi_hicp_log_spread,
            horizon,
            config.spread_half_life_months,
        )
        foi_annual_rate = hicp_annual_rate + spread_rate
        hicp_monthly = hicp_annual_rate / 12 + hicp_seasonal[date.month - 1]
        foi_monthly = foi_annual_rate / 12 + foi_seasonal[date.month - 1]
        levels *= np.exp([foi_monthly, hicp_monthly])
        rows.append(
            {
                "date": date,
                "foi_xt_it": levels[0],
                "hicp_xt_ea": levels[1],
                "foi_hicp_log_spread": spread_rate,
                "model_version": config.model_version,
            }
        )
    baseline = pd.DataFrame(rows)
    if not (baseline[["foi_xt_it", "hicp_xt_ea"]] > 0).all().all():
        raise ValueError("Baseline contains non-positive index levels.")
    # Report the actual 12-month index change implied by the generated levels,
    # including the transition from the final observed months into the forecast.
    # The internal anchor-and-decay rate is intentionally not exposed as YoY.
    for column, yoy_column in (("foi_xt_it", "foi_yoy"), ("hicp_xt_ea", "hicp_yoy")):
        observed = history[["date", column]].tail(12)
        combined = pd.concat([observed, baseline[["date", column]]], ignore_index=True)
        baseline[yoy_column] = combined[column].pct_change(12).iloc[12:].to_numpy()
    return baseline


def write_baseline() -> None:
    """Write the sole baseline output consumed by the LDI workflow."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    history = load_history()
    baseline = build_baseline(history, forecast_end_date())
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    baseline.to_parquet(OUTPUT_PATH, index=False, engine="pyarrow")
    logging.info("Baseline written: %s to %s", baseline.date.iloc[0], baseline.date.iloc[-1])
