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
from .inflation_baseline import BaselineConfig, build_baseline
from .utils import INFLATION_STRESS_SCENARIOS_PATH


INDEX_COLUMNS = ("foi_xt_it", "hicp_xt_ea")
BP_PER_UNIT = 10_000
MONTHS_PER_YEAR = 12
SCENARIO_FAMILIES = {"transitory", "persistent", "regime_shift"}
START_RULES = {"absolute", "forecast_start"}


@dataclass(frozen=True)
class InflationShock:
    """A versioned deterministic inflation stress definition.

    Transitory and persistent scenarios overlay shocks on the baseline monthly
    log changes. A regime shift instead re-anchors the long-run baseline
    inflation target, which avoids treating a structural assumption as an
    endlessly compounding temporary shock.
    """

    scenario_id: str
    start_date: pd.Timestamp | None = None
    common_annual_shock_bp: float = 0.0
    foi_hicp_spread_shock_bp: float = 0.0
    ramp_months: int = 6
    hold_months: int = 12
    decay_half_life_months: float = 24.0
    family: str = "transitory"
    severity: str = "adverse"
    start_rule: str = "absolute"
    start_offset_months: int = 0
    long_run_hicp_target: float | None = None
    long_run_foi_hicp_spread_bp: float | None = None
    convergence_half_life_months: float | None = None
    rationale: str = "Legacy deterministic stress definition."
    calibration_basis: str = "Legacy configuration; governance review required."
    review_frequency_months: int = 12
    model_version: str = "inflation_stress_v2"

    @classmethod
    def from_mapping(cls, values: dict) -> "InflationShock":
        required = {"scenario_id"}
        missing = required - set(values)
        if missing:
            raise ValueError(f"Inflation stress scenario is missing: {sorted(missing)}")
        return cls(
            scenario_id=str(values["scenario_id"]),
            start_date=(
                pd.Timestamp(values["start_date"])
                if values.get("start_date") is not None
                else None
            ),
            common_annual_shock_bp=float(values.get("common_annual_shock_bp", 0.0)),
            foi_hicp_spread_shock_bp=float(values.get("foi_hicp_spread_shock_bp", 0.0)),
            ramp_months=int(values.get("ramp_months", 6)),
            hold_months=int(values.get("hold_months", 12)),
            decay_half_life_months=float(values.get("decay_half_life_months", 24.0)),
            family=str(values.get("family", "transitory")),
            severity=str(values.get("severity", "adverse")),
            start_rule=str(values.get("start_rule", "absolute")),
            start_offset_months=int(values.get("start_offset_months", 0)),
            long_run_hicp_target=(
                float(values["long_run_hicp_target"])
                if values.get("long_run_hicp_target") is not None
                else None
            ),
            long_run_foi_hicp_spread_bp=(
                float(values["long_run_foi_hicp_spread_bp"])
                if values.get("long_run_foi_hicp_spread_bp") is not None
                else None
            ),
            convergence_half_life_months=(
                float(values["convergence_half_life_months"])
                if values.get("convergence_half_life_months") is not None
                else None
            ),
            rationale=str(values.get("rationale", "Legacy deterministic stress definition.")),
            calibration_basis=str(
                values.get("calibration_basis", "Legacy configuration; governance review required.")
            ),
            review_frequency_months=int(values.get("review_frequency_months", 12)),
            model_version=str(values.get("model_version", "inflation_stress_v2")),
        )

    def validate(self) -> None:
        if not self.scenario_id or self.scenario_id.strip() != self.scenario_id:
            raise ValueError("Inflation stress scenario_id must be a non-empty trimmed string.")
        if self.family not in SCENARIO_FAMILIES:
            raise ValueError(f"Inflation stress family must be one of: {sorted(SCENARIO_FAMILIES)}")
        if self.start_rule not in START_RULES:
            raise ValueError(f"Inflation stress start_rule must be one of: {sorted(START_RULES)}")
        if self.start_offset_months < 0:
            raise ValueError("Inflation stress start_offset_months cannot be negative.")
        if self.start_rule == "absolute":
            if self.start_date is None:
                raise ValueError("Absolute inflation stresses require start_date.")
            if self.start_date != self.start_date.normalize() or self.start_date.day != 1:
                raise ValueError("Inflation stress start_date must be a month start.")
        elif self.start_date is not None:
            if self.start_date != self.start_date.normalize() or self.start_date.day != 1:
                raise ValueError("Inflation stress start_date must be a month start when supplied.")
        values = (self.common_annual_shock_bp, self.foi_hicp_spread_shock_bp)
        if not np.isfinite(values).all():
            raise ValueError("Inflation stress shock sizes must be finite.")
        if not self.severity.strip() or not self.rationale.strip() or not self.calibration_basis.strip():
            raise ValueError("Inflation stresses require severity, rationale, and calibration_basis.")
        if self.review_frequency_months < 1:
            raise ValueError("Inflation stress review_frequency_months must be positive.")
        if self.family == "regime_shift":
            if self.start_rule != "forecast_start" or self.start_offset_months != 0:
                raise ValueError("Regime shifts must begin at forecast_start with no offset.")
            if self.common_annual_shock_bp or self.foi_hicp_spread_shock_bp:
                raise ValueError("Regime shifts cannot combine target changes with temporary shocks.")
            if self.long_run_hicp_target is None or not np.isfinite(self.long_run_hicp_target):
                raise ValueError("Regime shifts require a finite long_run_hicp_target.")
            if self.long_run_hicp_target <= -1:
                raise ValueError("Regime-shift long_run_hicp_target must exceed -100%.")
            if self.convergence_half_life_months is None or self.convergence_half_life_months <= 0:
                raise ValueError("Regime shifts require a positive convergence_half_life_months.")
            if self.long_run_foi_hicp_spread_bp is not None and not np.isfinite(
                self.long_run_foi_hicp_spread_bp
            ):
                raise ValueError("Regime-shift FOI/HICP spread target must be finite.")
        else:
            if self.ramp_months < 1 or self.hold_months < 0 or self.decay_half_life_months <= 0:
                raise ValueError("Inflation stress timing parameters are invalid.")
            if any(
                value is not None
                for value in (
                    self.long_run_hicp_target,
                    self.long_run_foi_hicp_spread_bp,
                    self.convergence_half_life_months,
                )
            ):
                raise ValueError("Only regime shifts may define long-run target overrides.")

    def resolved_start_date(self, forecast_start: pd.Timestamp) -> pd.Timestamp:
        """Resolve and validate the scenario start against this forecast run."""
        self.validate()
        forecast_start = pd.Timestamp(forecast_start).normalize()
        if self.start_rule == "absolute":
            return pd.Timestamp(self.start_date)
        return forecast_start + pd.offsets.MonthBegin(self.start_offset_months)

    def effective_horizon_months(self) -> int | None:
        """Return the material profile horizon; regime shifts remain structural."""
        if self.family == "regime_shift":
            return None
        return int(self.ramp_months + self.hold_months + np.ceil(3 * self.decay_half_life_months))


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
    required_values = ["date", *INDEX_COLUMNS]
    if checked.empty or checked[required_values].isna().any().any():
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
    dates = pd.to_datetime(dates)
    start_date = scenario.resolved_start_date(dates.min())
    elapsed = (
        pd.PeriodIndex(dates, freq="M").asi8
        - pd.Period(start_date, freq="M").ordinal
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


def _add_scenario_metadata(
    frame: pd.DataFrame, scenario: InflationShock, resolved_start_date: pd.Timestamp
) -> None:
    """Attach governance metadata to every path row for an auditable replay."""
    frame["scenario_id"] = scenario.scenario_id
    frame["scenario_family"] = scenario.family
    frame["severity"] = scenario.severity
    frame["resolved_start_date"] = resolved_start_date
    frame["effective_horizon_months"] = scenario.effective_horizon_months()
    frame["rationale"] = scenario.rationale
    frame["calibration_basis"] = scenario.calibration_basis
    frame["review_frequency_months"] = scenario.review_frequency_months
    frame["model_version"] = scenario.model_version


def _build_regime_shift_baseline(
    baseline: pd.DataFrame,
    history: pd.DataFrame,
    scenario: InflationShock,
    resolved_start_date: pd.Timestamp,
) -> pd.DataFrame:
    """Rebuild the forecast from history under a new long-run inflation anchor."""
    if resolved_start_date != baseline["date"].iloc[0]:
        raise ValueError("Regime shifts must begin at the first baseline forecast month.")
    config = BaselineConfig(
        annual_target=float(scenario.long_run_hicp_target),
        convergence_half_life_months=float(scenario.convergence_half_life_months),
        spread_half_life_months=float(scenario.convergence_half_life_months),
        long_run_foi_hicp_log_spread=float(
            (scenario.long_run_foi_hicp_spread_bp or 0.0) / BP_PER_UNIT
        ),
    )
    reanchored = build_baseline(history, baseline["date"].iloc[-1], config)
    _add_scenario_metadata(reanchored, scenario, resolved_start_date)
    reanchored["common_annual_shock_bp"] = 0.0
    reanchored["foi_hicp_spread_shock_bp"] = 0.0
    reanchored["long_run_hicp_target"] = scenario.long_run_hicp_target
    reanchored["long_run_foi_hicp_spread_bp"] = scenario.long_run_foi_hicp_spread_bp
    return reanchored


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

    resolved_start_date = scenario.resolved_start_date(baseline["date"].iloc[0])
    if scenario.family == "regime_shift":
        return _validate_path(
            _build_regime_shift_baseline(baseline, history, scenario, resolved_start_date),
            f"Stress scenario {scenario.scenario_id}",
        )

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
    _add_scenario_metadata(stressed, scenario, resolved_start_date)
    stressed["common_annual_shock_bp"] = common_profile
    stressed["foi_hicp_spread_shock_bp"] = spread_profile
    stressed["long_run_hicp_target"] = np.nan
    stressed["long_run_foi_hicp_spread_bp"] = np.nan
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
