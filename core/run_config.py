"""Validated, immutable parameters that may be changed by the web application."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

from .inflation_baseline import BaselineConfig
from .utils import (
    BROKER_FEE_RATE,
    BROKER_MAX_FEE,
    BROKER_MIN_FEE,
    COUPON_TAX_RATE,
    MAX_ISSUER_WEIGHT,
    MAX_NOMINAL_PER_BOND,
    MAX_POSITIONS,
    NOMINAL,
    TERMINAL_CAPITAL_RATIO,
)


@dataclass(frozen=True)
class RunParameters:
    """The deliberately small set of model controls exposed by the current UI."""

    nominal: float = NOMINAL
    max_nominal_per_bond: float = MAX_NOMINAL_PER_BOND
    coupon_tax_rate: float = COUPON_TAX_RATE
    max_issuer_weight: float = MAX_ISSUER_WEIGHT
    max_positions: int = MAX_POSITIONS
    terminal_capital_ratio: float = TERMINAL_CAPITAL_RATIO
    prefer_short_maturity: bool = True
    broker_fee_rate: float = BROKER_FEE_RATE
    broker_min_fee: float = BROKER_MIN_FEE
    broker_max_fee: float = BROKER_MAX_FEE
    baseline_annual_target: float = BaselineConfig().annual_target
    baseline_convergence_half_life_months: float = (
        BaselineConfig().convergence_half_life_months
    )
    baseline_spread_half_life_months: float = BaselineConfig().spread_half_life_months
    baseline_seasonal_years: int = BaselineConfig().seasonal_years

    @classmethod
    def from_mapping(cls, values: dict | None) -> "RunParameters":
        if values is None:
            return cls()
        if not isinstance(values, dict):
            raise ValueError("Parameters must be a JSON object.")
        allowed = {field.name for field in fields(cls)}
        if unknown := sorted(set(values) - allowed):
            raise ValueError(f"Unknown parameters: {unknown}")
        merged = {**cls().to_dict(), **values}
        if not isinstance(merged["prefer_short_maturity"], bool):
            raise ValueError("prefer_short_maturity must be boolean.")
        integer_fields = ("max_positions", "baseline_seasonal_years")
        for name in integer_fields:
            value = merged[name]
            if (
                isinstance(value, bool)
                or not _finite(value)
                or int(value) != float(value)
            ):
                raise ValueError(f"{name} must be an integer.")
            merged[name] = int(value)
        numeric_fields = allowed - set(integer_fields) - {"prefer_short_maturity"}
        for name in numeric_fields:
            if isinstance(merged[name], bool) or not _finite(merged[name]):
                raise ValueError(f"{name} must be a finite number.")
            merged[name] = float(merged[name])
        result = cls(**merged)
        result.validate()
        return result

    def validate(self) -> None:
        if self.nominal <= 0:
            raise ValueError("nominal must be positive.")
        ratio = self.max_nominal_per_bond / self.nominal
        if self.max_nominal_per_bond < self.nominal or not math.isclose(
            ratio, round(ratio), rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError(
                "max_nominal_per_bond must be at least nominal and an exact multiple of it."
            )
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1.")
        if not 0 < self.max_issuer_weight <= 1:
            raise ValueError("max_issuer_weight must be greater than 0 and at most 1.")
        if not 0 <= self.coupon_tax_rate <= 1:
            raise ValueError("coupon_tax_rate must be between 0 and 1.")
        if self.terminal_capital_ratio < 0:
            raise ValueError("terminal_capital_ratio must be non-negative.")
        if self.broker_fee_rate < 0:
            raise ValueError("broker_fee_rate must be non-negative.")
        if not 0 <= self.broker_min_fee <= self.broker_max_fee:
            raise ValueError(
                "Broker fees must satisfy 0 <= broker_min_fee <= broker_max_fee."
            )
        if self.baseline_annual_target <= -1:
            raise ValueError("baseline_annual_target must be greater than -1.")
        if self.baseline_convergence_half_life_months <= 0:
            raise ValueError("baseline_convergence_half_life_months must be positive.")
        if self.baseline_spread_half_life_months <= 0:
            raise ValueError("baseline_spread_half_life_months must be positive.")
        if self.baseline_seasonal_years < 3:
            raise ValueError("baseline_seasonal_years must be at least 3.")

    def baseline_config(self) -> BaselineConfig:
        return BaselineConfig(
            annual_target=self.baseline_annual_target,
            convergence_half_life_months=self.baseline_convergence_half_life_months,
            spread_half_life_months=self.baseline_spread_half_life_months,
            seasonal_years=self.baseline_seasonal_years,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class UniverseFilters:
    """Per-run restrictions applied after static bond cleaning."""

    allowed_ratings: tuple[str, ...] | None = None
    allowed_issuers: tuple[str, ...] | None = None
    include_inflation_linked: bool = True

    @classmethod
    def from_mapping(cls, values: dict | None) -> "UniverseFilters":
        if values is None:
            return cls()
        if not isinstance(values, dict):
            raise ValueError("universe_filters must be a JSON object.")
        unknown = set(values) - {
            "allowed_ratings",
            "allowed_issuers",
            "include_inflation_linked",
        }
        if unknown:
            raise ValueError(f"Unknown universe filters: {sorted(unknown)}")

        def normalise_list(name):
            value = values.get(name)
            if value is None:
                return None
            if not isinstance(value, (list, tuple, set)) or not value:
                raise ValueError(f"{name} must be null or a non-empty list.")
            result = tuple(
                sorted({str(item).strip() for item in value if str(item).strip()})
            )
            if not result:
                raise ValueError(f"{name} must contain at least one value.")
            return result

        include_ilb = values.get("include_inflation_linked", True)
        if not isinstance(include_ilb, bool):
            raise ValueError("include_inflation_linked must be boolean.")
        return cls(
            normalise_list("allowed_ratings"),
            normalise_list("allowed_issuers"),
            include_ilb,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
