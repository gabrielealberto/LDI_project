"""Joint long-horizon inflation scenarios for FOI Italy and euro-area HICP.

The module is deliberately standalone: it reads the versioned liability horizon and
the two downloaded index series, but is not called by the LDI pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
FOI_PATH = ROOT / "data" / "foi_xt_it.parquet"
HICP_PATH = ROOT / "data" / "hicp_xt_ea.parquet"
LIABILITIES_PATH = ROOT / "data" / "config" / "liabilities.json"
OUTPUT_DIR = ROOT / "data" / "processed"


@dataclass(frozen=True)
class ScenarioConfig:
    """Explicit calibration and simulation controls."""

    annual_target_foi: float = 0.02
    annual_target_hicp: float = 0.02
    paths: int = 10_000
    block_months: int = 6
    ridge: float = 1e-5
    maximum_persistence: float = 0.92
    residual_shock_scale: float = 2.0
    seed: int = 20260904
    backtest_paths: int = 2_000
    backtest_minimum_training_months: int = 120
    backtest_stride_months: int = 12


def liability_end_date(path: Path = LIABILITIES_PATH) -> pd.Timestamp:
    """Return the latest liability end date, failing for malformed configuration."""
    records = json.loads(path.read_text(encoding="utf-8"))
    dates = pd.to_datetime([record["end_date"] for record in records], errors="raise")
    if len(dates) == 0:
        raise ValueError("Liability configuration contains no end_date values.")
    return pd.Timestamp(dates.max()).replace(day=1)


def load_history() -> pd.DataFrame:
    """Load, align, and validate the two official monthly index series."""
    foi = pd.read_parquet(FOI_PATH)[["date", "foi_xt_it"]]
    hicp = pd.read_parquet(HICP_PATH)[["date", "hicp_xt_ea"]]
    history = foi.merge(hicp, on="date", how="inner").sort_values("date").reset_index(drop=True)
    if len(history) < 120 or history.isna().any().any():
        raise ValueError("At least ten clean years of overlapping FOI and HICP are required.")
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
    seasonal = np.vstack([returns[month == m].mean(axis=0) for m in range(1, 13)])
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
    history: pd.DataFrame,
    end_date: pd.Timestamp,
    config: ScenarioConfig,
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, dict[str, float]]:
    """Generate correlated index paths via a residual block bootstrap."""
    coefficient, residuals, seasonal = fit_model(history, config)
    first = pd.Timestamp(history["date"].iloc[-1]) + pd.offsets.MonthBegin(1)
    dates = pd.date_range(first, end_date, freq="MS")
    if len(dates) == 0:
        raise ValueError("Liability horizon must extend beyond the latest historical observation.")
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
        monthly_return = targets + seasonal[date.month - 1] + state
        current = current + monthly_return
        log_levels[step] = current
    paths = np.exp(log_levels)
    metadata = {
        "first_forecast_date": str(dates[0].date()),
        "last_forecast_date": str(dates[-1].date()),
        "var_spectral_radius": float(np.max(np.abs(np.linalg.eigvals(coefficient)))),
        "residual_correlation": float(np.corrcoef(residuals.T)[0, 1]),
    }
    return dates, paths, coefficient, metadata


def summarise(dates: pd.DatetimeIndex, paths: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create percentile bands and coherent representative scenario paths."""
    quantiles = np.quantile(paths, [0.05, 0.25, 0.5, 0.75, 0.95], axis=1)
    summary = pd.DataFrame({"date": dates})
    for index, name in enumerate(("foi_xt_it", "hicp_xt_ea")):
        for q, label in enumerate(("p05", "p25", "p50", "p75", "p95")):
            summary[f"{name}_{label}"] = quantiles[q, :, index]
    terminal = paths[-1, :, 0]
    selected = {
        "low_inflation": int(np.argmin(abs(terminal - np.quantile(terminal, 0.10)))),
        "baseline": int(np.argmin(abs(terminal - np.quantile(terminal, 0.50)))),
        "high_inflation": int(np.argmin(abs(terminal - np.quantile(terminal, 0.90)))),
        "severe_inflation": int(np.argmin(abs(terminal - np.quantile(terminal, 0.99)))),
    }
    rows = []
    for scenario, path_id in selected.items():
        for date, values in zip(dates, paths[:, path_id], strict=True):
            rows.append(
                {
                    "date": date,
                    "scenario": scenario,
                    "path_id": path_id,
                    "foi_xt_it": values[0],
                    "hicp_xt_ea": values[1],
                    "is_forecast": True,
                }
            )
    return summary, pd.DataFrame(rows)


def plot_scenarios(
    history: pd.DataFrame, summary: pd.DataFrame, scenarios: pd.DataFrame, path: Path
) -> None:
    """Plot history, uncertainty bands, and selected coherent scenarios."""
    figure, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True, constrained_layout=True)
    for axis, column, title in zip(
        axes, ("foi_xt_it", "hicp_xt_ea"), ("FOI Italia", "HICP area euro"), strict=True
    ):
        axis.plot(history["date"], history[column], color="#263238", label="Storico")
        axis.fill_between(
            summary["date"],
            summary[f"{column}_p05"],
            summary[f"{column}_p95"],
            color="#5b8ff9",
            alpha=0.18,
            label="P5–P95",
        )
        axis.plot(summary["date"], summary[f"{column}_p50"], color="#1f5aa6", lw=2, label="Mediana")
        for scenario, group in scenarios.groupby("scenario"):
            axis.plot(
                group["date"], group[column], lw=1, alpha=0.8, label=scenario.replace("_", " ")
            )
        axis.set_title(title)
        axis.set_ylabel("Indice (base 2025=100)")
        axis.grid(alpha=0.25)
        axis.legend(ncol=3, fontsize=8)
    figure.suptitle("Scenari congiunti FOI–HICP fino alla massima liability", fontsize=15)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=180)
    plt.close(figure)


def rolling_backtest(history: pd.DataFrame, config: ScenarioConfig) -> tuple[pd.DataFrame, dict]:
    """Backtest 12/24-month forecasts using only information known at each origin."""
    horizons = (12, 24)
    first = config.backtest_minimum_training_months - 1
    last = len(history) - max(horizons) - 1
    if last < first:
        raise ValueError("Insufficient overlapping history for rolling backtest.")
    test_config = replace(config, paths=config.backtest_paths)
    rows = []
    for origin in range(first, last + 1, config.backtest_stride_months):
        training = history.iloc[: origin + 1].copy()
        for horizon in horizons:
            actual = history.iloc[origin + horizon]
            _, paths, _, _ = simulate_paths(training, actual["date"], test_config)
            for column, index in (("foi_xt_it", 0), ("hicp_xt_ea", 1)):
                p05, median, p95 = np.quantile(paths[-1, :, index], [0.05, 0.5, 0.95])
                value, naive = float(actual[column]), float(training[column].iloc[-1])
                rows.append(
                    {
                        "origin": training["date"].iloc[-1],
                        "target_date": actual["date"],
                        "series": column,
                        "horizon_months": horizon,
                        "actual": value,
                        "median": median,
                        "p05": p05,
                        "p95": p95,
                        "naive": naive,
                        "squared_error": (median - value) ** 2,
                        "naive_squared_error": (naive - value) ** 2,
                        "covered_p05_p95": bool(p05 <= value <= p95),
                    }
                )
    results = pd.DataFrame(rows)
    metrics = results.groupby(["series", "horizon_months"], as_index=False).agg(
        origins=("origin", "size"),
        rmse=("squared_error", lambda x: float(np.sqrt(np.mean(x)))),
        naive_rmse=("naive_squared_error", lambda x: float(np.sqrt(np.mean(x)))),
        coverage_p05_p95=("covered_p05_p95", "mean"),
    )
    passed = bool(
        (metrics["rmse"] <= metrics["naive_rmse"]).all()
        and (metrics["coverage_p05_p95"] >= 0.70).all()
    )
    if not passed:
        logging.warning(
            "Backtest did not pass all reliability gates; inspect RMSE and interval coverage."
        )
    logging.info("Rolling backtest completed: %s forecast observations.", len(results))
    return results, {"passed": passed, "metrics": metrics.to_dict(orient="records")}


def main() -> None:
    """Build independent quantitative scenarios and their audit outputs."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = ScenarioConfig()
    history = load_history()
    end_date = liability_end_date()
    dates, paths, _, metadata = simulate_paths(history, end_date, config)
    summary, scenarios = summarise(dates, paths)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = OUTPUT_DIR / "inflation_scenario_summary.parquet"
    scenarios_path = OUTPUT_DIR / "inflation_scenarios.parquet"
    backtest_path = OUTPUT_DIR / "inflation_scenario_backtest.parquet"
    plot_path = OUTPUT_DIR / "inflation_scenarios.png"
    summary.to_parquet(summary_path, index=False, engine="pyarrow")
    scenarios.to_parquet(scenarios_path, index=False, engine="pyarrow")
    backtest, backtest_summary = rolling_backtest(history, config)
    backtest.to_parquet(backtest_path, index=False, engine="pyarrow")
    plot_scenarios(history, summary, scenarios, plot_path)
    metadata.update(
        {
            "config": asdict(config),
            "history_start": str(history.date.min().date()),
            "history_end": str(history.date.max().date()),
            "liability_end": str(end_date.date()),
            "backtest": backtest_summary,
        }
    )
    (OUTPUT_DIR / "inflation_scenario_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(f"Forecast: {dates[0]:%Y-%m} to {dates[-1]:%Y-%m}; paths: {config.paths}")
    print(
        f"Summary: {summary_path}\nScenarios: {scenarios_path}\nBacktest: {backtest_path}\nPlot: {plot_path}"
    )


if __name__ == "__main__":
    main()
