"""Build coherent long-horizon FOI Italy and euro-area HICP paths."""

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
OUTPUT_PATH = ROOT / "data" / "processed" / "inflation_scenarios.parquet"


@dataclass(frozen=True)
class ScenarioConfig:
    annual_target_foi: float = 0.02
    annual_target_hicp: float = 0.02
    paths: int = 10_000
    block_months: int = 6
    ridge: float = 1e-5
    maximum_persistence: float = 0.92
    residual_shock_scale: float = 2.0
    seed: int = 20260904


def forecast_end_date(
    liabilities_path: Path = LIABILITIES_PATH,
    inflation_linked_path: Path = INFLATION_LINKED_PATH,
) -> pd.Timestamp:
    """Cover all configured liability payments and indexed redemptions."""
    liabilities = json.loads(liabilities_path.read_text(encoding="utf-8"))
    liability_end = pd.Timestamp(max(record["end_date"] for record in liabilities)).replace(day=1)
    bonds = json.loads(inflation_linked_path.read_text(encoding="utf-8"))
    latest_maturity = pd.Timestamp(max(term["maturity_date"] for term in bonds.values())).replace(
        day=1
    )
    return max(liability_end, latest_maturity)


def load_history() -> pd.DataFrame:
    """Load and validate the overlapping official monthly index observations."""
    foi = pd.read_parquet(FOI_PATH)[["date", "foi_xt_it"]]
    hicp = pd.read_parquet(HICP_PATH)[["date", "hicp_xt_ea"]]
    history = foi.merge(hicp, on="date", how="inner").sort_values("date").reset_index(drop=True)
    if len(history) < 120 or history.isna().any().any():
        raise ValueError("At least ten clean years of overlapping FOI/HICP are required.")
    if not (history[["foi_xt_it", "hicp_xt_ea"]] > 0).all().all():
        raise ValueError("Inflation index levels must be strictly positive.")
    months = pd.PeriodIndex(history["date"], freq="M").asi8
    if not np.all(np.diff(months) == 1):
        raise ValueError("Overlapping FOI/HICP history must be monthly and gap-free.")
    return history


def fit_model(
    history: pd.DataFrame, config: ScenarioConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fit a seasonally adjusted, ridge-regularised bivariate VAR(1)."""
    levels = history[["foi_xt_it", "hicp_xt_ea"]].to_numpy(dtype=float)
    returns = np.diff(np.log(levels), axis=0)
    month = history["date"].dt.month.to_numpy()[1:]
    seasonal = np.vstack([returns[month == value].mean(axis=0) for value in range(1, 13)])
    seasonal -= seasonal.mean(axis=0, keepdims=True)
    targets = np.log1p([config.annual_target_foi, config.annual_target_hicp]) / 12
    state = returns - seasonal[month - 1] - targets
    x, y = state[:-1], state[1:]
    coefficient = np.linalg.solve(x.T @ x + config.ridge * np.eye(2), x.T @ y)
    eigenvalue = np.max(np.abs(np.linalg.eigvals(coefficient)))
    if eigenvalue > config.maximum_persistence:
        coefficient *= config.maximum_persistence / eigenvalue
    residuals = y - x @ coefficient
    residuals -= residuals.mean(axis=0, keepdims=True)
    if len(residuals) < config.block_months:
        raise ValueError("Insufficient residual history for the requested bootstrap block size.")
    return coefficient, residuals, seasonal


def simulate_paths(
    history: pd.DataFrame, end_date: pd.Timestamp, config: ScenarioConfig
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Generate correlated paths with a residual block bootstrap."""
    coefficient, residuals, seasonal = fit_model(history, config)
    first = pd.Timestamp(history["date"].iloc[-1]) + pd.offsets.MonthBegin(1)
    dates = pd.date_range(first, end_date, freq="MS")
    if len(dates) == 0:
        raise ValueError("Forecast horizon must extend beyond the latest historical observation.")
    rng = np.random.default_rng(config.seed)
    n_steps, n_paths = len(dates), config.paths
    state = np.zeros((n_paths, 2))
    log_levels = np.empty((n_steps, n_paths, 2))
    current = np.log(history[["foi_xt_it", "hicp_xt_ea"]].iloc[-1].to_numpy(dtype=float))
    starts = rng.integers(
        0, len(residuals) - config.block_months + 1, size=(n_steps + config.block_months, n_paths)
    )
    shocks = np.empty((n_steps, n_paths, 2))
    for step in range(0, n_steps, config.block_months):
        size = min(config.block_months, n_steps - step)
        positions = starts[step, :, None] + np.arange(size)
        shocks[step : step + size] = residuals[positions].transpose(1, 0, 2)
    targets = np.log1p([config.annual_target_foi, config.annual_target_hicp]) / 12
    for step, date in enumerate(dates):
        state = state @ coefficient + config.residual_shock_scale * shocks[step]
        current = current + targets + seasonal[date.month - 1] + state
        log_levels[step] = current
    return dates, np.exp(log_levels)


def select_paths(dates: pd.DatetimeIndex, paths: np.ndarray) -> pd.DataFrame:
    """Select coherent low, baseline, high, and severe paths by terminal FOI."""
    terminal = paths[-1, :, 0]
    quantiles = {
        "low_inflation": 0.10,
        "baseline": 0.50,
        "high_inflation": 0.90,
        "severe_inflation": 0.99,
    }
    rows = []
    for scenario, quantile in quantiles.items():
        path_id = int(np.argmin(abs(terminal - np.quantile(terminal, quantile))))
        for date, values in zip(dates, paths[:, path_id], strict=True):
            rows.append(
                {
                    "date": date,
                    "scenario": scenario,
                    "foi_xt_it": values[0],
                    "hicp_xt_ea": values[1],
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    """Write the sole scenario output consumed by the LDI workflow."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = ScenarioConfig()
    dates, paths = simulate_paths(load_history(), forecast_end_date(), config)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    select_paths(dates, paths).to_parquet(OUTPUT_PATH, index=False, engine="pyarrow")
    print(f"Forecast: {dates[0]:%Y-%m} to {dates[-1]:%Y-%m}; paths: {config.paths}")
    print(f"Scenarios: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
