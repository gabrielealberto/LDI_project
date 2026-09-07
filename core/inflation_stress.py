"""Deterministic, auditable inflation stress paths around the LDI baseline.

The module applies shocks to monthly log changes, never directly to index
levels.  This preserves continuity, positivity, and the baseline seasonal
profile while allowing a common HICP/FOI shock and a separate Italy spread
shock.  It intentionally contains no probabilities or optimisation logic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .inflation_linked_cashflows import BaselineIndexProvider, build_index_provider
from .utils import INFLATION_STRESS_SCENARIOS_PATH


INDEX_COLUMNS = ("foi_xt_it", "hicp_xt_ea")
BP_PER_UNIT = 10_000
MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class InflationShock:
    """A transparent shock to annualised inflation and the FOI/HICP spread."""

    scenario_id: str
    start_date: pd.Timestamp
    common_annual_shock_bp: float = 0.0
    foi_hicp_spread_shock_bp: float = 0.0
    ramp_months: int = 6
    hold_months: int = 12
    decay_half_life_months: float = 24.0
    model_version: str = "inflation_stress_v1"

    @classmethod
    def from_mapping(cls, values: dict) -> "InflationShock":
        required = {"scenario_id", "start_date"}
        missing = required - set(values)
        if missing:
            raise ValueError(f"Inflation stress scenario is missing: {sorted(missing)}")
        return cls(
            scenario_id=str(values["scenario_id"]),
            start_date=pd.Timestamp(values["start_date"]),
            common_annual_shock_bp=float(values.get("common_annual_shock_bp", 0.0)),
            foi_hicp_spread_shock_bp=float(values.get("foi_hicp_spread_shock_bp", 0.0)),
            ramp_months=int(values.get("ramp_months", 6)),
            hold_months=int(values.get("hold_months", 12)),
            decay_half_life_months=float(values.get("decay_half_life_months", 24.0)),
            model_version=str(values.get("model_version", "inflation_stress_v1")),
        )

    def validate(self) -> None:
        if not self.scenario_id or self.scenario_id.strip() != self.scenario_id:
            raise ValueError("Inflation stress scenario_id must be a non-empty trimmed string.")
        if self.start_date != self.start_date.normalize() or self.start_date.day != 1:
            raise ValueError("Inflation stress start_date must be a month start.")
        values = (self.common_annual_shock_bp, self.foi_hicp_spread_shock_bp)
        if not np.isfinite(values).all():
            raise ValueError("Inflation stress shock sizes must be finite.")
        if self.ramp_months < 1 or self.hold_months < 0 or self.decay_half_life_months <= 0:
            raise ValueError("Inflation stress timing parameters are invalid.")


def load_inflation_stresses(
    path: Path = INFLATION_STRESS_SCENARIOS_PATH,
) -> list[InflationShock]:
    """Load versioned deterministic stress definitions without probabilities."""
    with Path(path).open(encoding="utf-8") as file:
        values = json.load(file)
    if not isinstance(values, list):
        raise ValueError("Inflation stress configuration must be a list.")
    scenarios = [InflationShock.from_mapping(value) for value in values]
    for scenario in scenarios:
        scenario.validate()
    scenario_ids = [scenario.scenario_id for scenario in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("Inflation stress scenario_id values must be unique.")
    return scenarios


def baseline_fingerprint(baseline: pd.DataFrame) -> str:
    """Return a stable fingerprint of the path used by a stress run."""
    required = {"date", *INDEX_COLUMNS}
    if missing := required - set(baseline):
        raise ValueError(f"Inflation baseline is missing columns: {sorted(missing)}")
    frame = baseline[["date", *INDEX_COLUMNS]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    digest = pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()
    return hashlib.sha256(digest).hexdigest()


def _validate_path(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    required = {"date", *INDEX_COLUMNS}
    if missing := required - set(frame):
        raise ValueError(f"{name} is missing columns: {sorted(missing)}")
    checked = frame.copy()
    checked["date"] = pd.to_datetime(checked["date"], errors="coerce")
    checked[list(INDEX_COLUMNS)] = checked[list(INDEX_COLUMNS)].apply(
        pd.to_numeric, errors="coerce"
    )
    if checked.empty or checked.isna().any().any():
        raise ValueError(f"{name} contains invalid values.")
    if checked["date"].duplicated().any() or not checked["date"].dt.is_month_start.all():
        raise ValueError(f"{name} must have unique month-start dates.")
    checked = checked.sort_values("date").reset_index(drop=True)
    expected = pd.date_range(checked["date"].iloc[0], checked["date"].iloc[-1], freq="MS")
    if not checked["date"].equals(pd.Series(expected)):
        raise ValueError(f"{name} must be monthly and gap-free.")
    if not np.isfinite(checked[list(INDEX_COLUMNS)].to_numpy()).all() or not (
        checked[list(INDEX_COLUMNS)] > 0
    ).all().all():
        raise ValueError(f"{name} index levels must be finite and positive.")
    return checked


def shock_profile_bp(dates: pd.Series, scenario: InflationShock) -> np.ndarray:
    """Return the annualised shock profile in bp for each month.

    The first shocked month begins a linear ramp, followed by an optional hold
    and exponential decay.  A zero shock produces an exact all-zero profile.
    """
    scenario.validate()
    elapsed = (
        pd.PeriodIndex(pd.to_datetime(dates), freq="M").asi8
        - pd.Period(scenario.start_date, freq="M").ordinal
    )
    profile = np.zeros(len(elapsed), dtype=float)
    active = elapsed >= 0
    months = elapsed[active] + 1
    ramp = np.minimum(months / scenario.ramp_months, 1.0)
    after_hold = np.maximum(months - scenario.ramp_months - scenario.hold_months, 0)
    profile[active] = ramp * 0.5 ** (after_hold / scenario.decay_half_life_months)
    return profile


def _add_yoy_columns(stressed: pd.DataFrame, history: pd.DataFrame) -> None:
    for column, yoy_column in (("foi_xt_it", "foi_yoy"), ("hicp_xt_ea", "hicp_yoy")):
        combined = pd.concat(
            [history[["date", column]].tail(12), stressed[["date", column]]],
            ignore_index=True,
        )
        stressed[yoy_column] = combined[column].pct_change(12).iloc[12:].to_numpy()


def build_stressed_baseline(
    baseline: pd.DataFrame, history: pd.DataFrame, scenario: InflationShock
) -> pd.DataFrame:
    """Apply one shock to a baseline while retaining its monthly structure."""
    scenario.validate()
    baseline = _validate_path(baseline, "Inflation baseline")
    history = _validate_path(history, "Inflation history")
    expected_start = history["date"].iloc[-1] + pd.offsets.MonthBegin(1)
    if baseline["date"].iloc[0] != expected_start:
        raise ValueError("Inflation baseline must begin in the month after the final history row.")

    common_profile = shock_profile_bp(baseline["date"], scenario)
    spread_profile = common_profile * 0
    if scenario.foi_hicp_spread_shock_bp:
        spread_profile = shock_profile_bp(baseline["date"], scenario)
    common_profile *= scenario.common_annual_shock_bp
    spread_profile *= scenario.foi_hicp_spread_shock_bp
    stressed = baseline.copy()

    # Retain baseline values exactly until the first non-zero shock.  From that
    # point forward, reconstruct levels from the baseline monthly log changes.
    active = (common_profile != 0) | (spread_profile != 0)
    if active.any():
        first_active = int(np.flatnonzero(active)[0])
        for column, shock in (
            ("hicp_xt_ea", common_profile),
            ("foi_xt_it", common_profile + spread_profile),
        ):
            if not np.any(shock):
                continue
            base_values = baseline[column].to_numpy(dtype=float)
            values = base_values.copy()
            prior_base = (
                float(history[column].iloc[-1])
                if first_active == 0
                else base_values[first_active - 1]
            )
            prior_stressed = prior_base
            for position in range(first_active, len(values)):
                base_prior = prior_base if position == first_active else base_values[position - 1]
                base_change = np.log(base_values[position] / base_prior)
                prior_stressed *= np.exp(
                    base_change + shock[position] / (BP_PER_UNIT * MONTHS_PER_YEAR)
                )
                values[position] = prior_stressed
            stressed[column] = values

    if "foi_hicp_log_spread" in stressed:
        stressed["foi_hicp_log_spread"] = (
            pd.to_numeric(stressed["foi_hicp_log_spread"], errors="coerce")
            + spread_profile / BP_PER_UNIT
        )
    stressed["scenario_id"] = scenario.scenario_id
    stressed["common_annual_shock_bp"] = common_profile
    stressed["foi_hicp_spread_shock_bp"] = spread_profile
    stressed["model_version"] = scenario.model_version
    _add_yoy_columns(stressed, history)
    return _validate_path(stressed, f"Stress scenario {scenario.scenario_id}")


def build_stressed_index_provider(
    baseline: pd.DataFrame,
    history: pd.DataFrame,
    scenario: InflationShock,
    foi_path: Path,
    hicp_path: Path,
) -> tuple[BaselineIndexProvider, pd.DataFrame]:
    """Return one shared provider and its auditable shocked forecast path."""
    stressed = build_stressed_baseline(baseline, history, scenario)
    return build_index_provider(stressed, foi_path=foi_path, hicp_path=hicp_path), stressed
