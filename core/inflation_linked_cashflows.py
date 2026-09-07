"""Baseline-indexed contractual cash flows for Italian inflation-linked BTPs.

The market parquet remains the source of the quoted clean price and valuation
date.  This module only turns the contractual terms in the versioned JSON plus
the coherent FOI/HICP baseline into the native ``l1/l2/l3`` cash-flow
schema consumed by the LDI optimiser.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .utils import INFLATION_BASELINE_PATH, PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "data" / "config" / "inflation_linked_bonds.json"
FOI_PATH = PROJECT_ROOT / "data" / "foi_xt_it.parquet"
HICP_PATH = PROJECT_ROOT / "data" / "hicp_xt_ea.parquet"
INDEX_COLUMNS = {"FOI_XT_IT": "foi_xt_it", "HICP_XT_EA": "hicp_xt_ea"}


class BaselineIndexProvider:
    """Serve realised monthly observations and the single forecast baseline."""

    def __init__(self, levels: dict[str, pd.Series]):
        self.levels = levels

    def monthly_level(self, index_id: str, date: pd.Timestamp) -> float:
        if index_id not in self.levels:
            raise ValueError(f"Unsupported inflation index {index_id!r}.")
        month = pd.Timestamp(date).replace(day=1)
        try:
            value = float(self.levels[index_id].loc[month])
        except KeyError as error:
            raise ValueError(
                f"{index_id} has no realised/baseline observation for {month:%Y-%m}."
            ) from error
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid {index_id} level for {month:%Y-%m}.")
        return value

    def reference_level(self, index_id: str, date: pd.Timestamp, lag_months: int = 3) -> float:
        """Return the lagged, linearly interpolated daily reference index.

        The MEF coefficient tables use the index three months before the payment
        month and linearly interpolate towards the following monthly observation.
        """
        date = pd.Timestamp(date).normalize()
        first = date.replace(day=1) - pd.DateOffset(months=lag_months)
        next_month = first + pd.offsets.MonthBegin(1)
        start = self.monthly_level(index_id, first)
        end = self.monthly_level(index_id, next_month)
        fraction = (date.day - 1) / date.days_in_month
        return start + fraction * (end - start)


def _read_levels(path: Path, column: str) -> pd.Series:
    frame = pd.read_parquet(path, columns=["date", column]).copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame["date"].duplicated().any() or not frame["date"].dt.is_month_start.all():
        raise ValueError(f"Invalid monthly dates in {path}.")
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any() or not (values > 0).all():
        raise ValueError(f"Invalid index levels in {path}.")
    return pd.Series(values.to_numpy(dtype=float), index=frame["date"], name=column).sort_index()


def build_index_provider(
    baseline: pd.DataFrame,
    foi_path: Path = FOI_PATH,
    hicp_path: Path = HICP_PATH,
) -> BaselineIndexProvider:
    """Build an index provider from history and a validated forecast path."""
    required = {"date", *INDEX_COLUMNS.values()}
    missing = required - set(baseline.columns)
    if missing:
        raise ValueError(f"Inflation baseline is missing columns: {sorted(missing)}")
    baseline = baseline[["date", *INDEX_COLUMNS.values()]].copy()
    baseline["date"] = pd.to_datetime(baseline["date"])
    if baseline.empty or baseline["date"].duplicated().any() or not baseline["date"].dt.is_month_start.all():
        raise ValueError("Inflation baseline does not have unique month-start dates.")

    history = {
        "FOI_XT_IT": _read_levels(foi_path, "foi_xt_it"),
        "HICP_XT_EA": _read_levels(hicp_path, "hicp_xt_ea"),
    }
    levels = {}
    for index_id, column in INDEX_COLUMNS.items():
        forecast = pd.Series(
            pd.to_numeric(baseline[column], errors="coerce").to_numpy(dtype=float),
            index=baseline["date"],
            name=column,
        ).sort_index()
        if forecast.isna().any() or not (forecast > 0).all():
            raise ValueError(f"Inflation baseline has invalid {index_id} levels.")
        overlap = history[index_id].index.intersection(forecast.index)
        if len(overlap):
            raise ValueError(f"History and baseline overlap for {index_id}: {overlap[0]:%Y-%m}.")
        combined = pd.concat([history[index_id], forecast]).sort_index()
        expected = pd.date_range(combined.index.min(), combined.index.max(), freq="MS")
        if not combined.index.equals(expected):
            raise ValueError(f"History/baseline series has a monthly gap for {index_id}.")
        levels[index_id] = combined
    return BaselineIndexProvider(levels)


def load_baseline_index_provider(
    foi_path: Path = FOI_PATH,
    hicp_path: Path = HICP_PATH,
    baseline_path: Path = INFLATION_BASELINE_PATH,
) -> BaselineIndexProvider:
    """Load history plus the sole coherent FOI/HICP forecast baseline."""
    return build_index_provider(
        pd.read_parquet(baseline_path), foi_path=foi_path, hicp_path=hicp_path
    )


def load_inflation_linked_terms(path: Path = CONFIG_PATH) -> dict[str, dict]:
    with Path(path).open(encoding="utf-8") as file:
        terms = json.load(file)
    if not isinstance(terms, dict):
        raise ValueError("Inflation-linked configuration must be an ISIN mapping.")
    for isincode, term in terms.items():
        required = {
            "type",
            "nominal_per_lot",
            "issue_date",
            "maturity_date",
            "cashflow_schedule",
            "indexation",
        }
        missing = required - set(term)
        if missing:
            raise ValueError(f"{isincode} is missing contractual fields: {sorted(missing)}")
    return terms


def _coupon_dates(term: dict) -> list[pd.Timestamp]:
    schedule = term["cashflow_schedule"]
    frequency = int(schedule["frequency_per_year"])
    if frequency <= 0 or 12 % frequency:
        raise ValueError("Cash-flow frequency must divide twelve months.")
    first = pd.Timestamp(schedule["first_coupon_date"])
    maturity = pd.Timestamp(term["maturity_date"])
    dates = []
    date = first
    while date <= maturity:
        dates.append(date)
        date += pd.DateOffset(months=12 // frequency)
    if not dates or dates[-1] != maturity:
        raise ValueError(f"Coupon schedule does not end at maturity {maturity:%Y-%m-%d}.")
    return dates


def _accrual_fraction(term: dict, coupon_date: pd.Timestamp, position: int) -> float:
    schedule = term["cashflow_schedule"]
    if position == 0 and "first_coupon_accrual_fraction" in schedule:
        numerator, denominator = schedule["first_coupon_accrual_fraction"].split("/")
        return float(numerator) / float(denominator)
    return 1 / int(schedule["frequency_per_year"])


def _period_base(
    coupon_dates: list[pd.Timestamp], position: int, issue_date: pd.Timestamp
) -> pd.Timestamp:
    return issue_date if position == 0 else coupon_dates[position - 1]


def _index_ratio(
    provider: BaselineIndexProvider, term: dict, date: pd.Timestamp, base: pd.Timestamp
) -> float:
    indexation = term["indexation"]
    lag = int(indexation.get("observation_lag_months", 3))
    return provider.reference_level(indexation["index_id"], date, lag) / provider.reference_level(
        indexation["index_id"], base, lag
    )


def _future_cashflows(
    term: dict, provider: BaselineIndexProvider, valuation_date: pd.Timestamp
) -> pd.DataFrame:
    """Create future contractual payments; l3 is the taxable non-principal amount."""
    nominal = float(term["nominal_per_lot"])
    issue_date = pd.Timestamp(term["issue_date"])
    maturity = pd.Timestamp(term["maturity_date"])
    coupons = _coupon_dates(term)
    indexation = term["indexation"]
    rows = []
    high_water = 1.0
    issue_reference = provider.reference_level(
        indexation["index_id"], issue_date, int(indexation.get("observation_lag_months", 3))
    )

    for position, date in enumerate(coupons):
        base = _period_base(coupons, position, issue_date)
        period_ratio = _index_ratio(provider, term, date, base)
        cumulative_ratio = (
            provider.reference_level(
                indexation["index_id"], date, int(indexation.get("observation_lag_months", 3))
            )
            / issue_reference
        )
        accrual = _accrual_fraction(term, date, position)
        principal, taxable = 0.0, 0.0

        if term["type"] == "btpei":
            coefficient = cumulative_ratio
            taxable = nominal * float(term["real_annual_coupon_rate"]) * accrual * coefficient
            if date == maturity:
                principal = nominal
                taxable += nominal * max(coefficient - 1.0, 0.0)
        elif term["type"] == "btp_italia":
            coupon = (
                nominal * float(term["real_annual_coupon_rate"]) * accrual * max(period_ratio, 1.0)
            )
            new_high_water = max(high_water, cumulative_ratio)
            revaluation = nominal * (new_high_water - high_water)
            high_water = new_high_water
            taxable = coupon + revaluation
            if date == maturity:
                principal = nominal
        elif term["type"] == "btp_italia_si":
            fixed = nominal * float(term["fixed_annual_coupon_rate"]) * accrual
            taxable = fixed + nominal * max(period_ratio - 1.0, 0.0)
            if date == maturity:
                principal = nominal
        else:
            raise ValueError(f"Unsupported inflation-linked payoff type {term['type']!r}.")

        if date > valuation_date:
            rows.append({"date": date, "l1": principal, "l2": 0.0, "l3": taxable})

    return pd.DataFrame(rows, columns=["date", "l1", "l2", "l3"])


def _purchase_cashflow(
    term: dict, market: pd.Series, provider: BaselineIndexProvider
) -> pd.DataFrame:
    valuation = pd.to_datetime(market["referencedate"], dayfirst=True).normalize()
    nominal = float(term["nominal_per_lot"])
    coupons = _coupon_dates(term)
    issue = pd.Timestamp(term["issue_date"])
    prior = [date for date in coupons if date <= valuation]
    accrual_base = prior[-1] if prior else issue
    if term["type"] == "btp_italia_si":
        # Its inflation spread is a coupon component, not a capital indexation.
        factor = 1.0
    else:
        base = (
            pd.Timestamp(term["indexation"]["base_reference_date"])
            if term["type"] == "btpei"
            else accrual_base
        )
        factor = _index_ratio(provider, term, valuation, base)
    next_dates = [date for date in coupons if date > valuation]
    next_coupon = next_dates[0] if next_dates else None
    accrued = 0.0
    if next_coupon is not None:
        annual_rate = float(
            term.get("real_annual_coupon_rate", term.get("fixed_annual_coupon_rate", 0.0))
        )
        accrual_days = (valuation - accrual_base).days
        period_days = (next_coupon - accrual_base).days
        frequency = int(term["cashflow_schedule"]["frequency_per_year"])
        if term["type"] == "btp_italia_si":
            # The prospectus applies the non-negative inflation component to
            # accrued interest as well as to the paid semi-annual coupon.
            accrued_rate = annual_rate / frequency + max(
                _index_ratio(provider, term, valuation, accrual_base) - 1.0, 0.0
            )
            accrued = nominal * accrued_rate * accrual_days / period_days
        else:
            accrued = (
                nominal * annual_rate / frequency * accrual_days / period_days * max(factor, 1.0)
            )
    return pd.DataFrame(
        [
            {
                "date": valuation,
                "l1": -nominal * float(market["price"]) / 100 * factor,
                "l2": -accrued,
                "l3": 0.0,
            }
        ]
    )


def build_inflation_linked_cashflows(
    bonds: pd.DataFrame,
    provider: BaselineIndexProvider | None = None,
    terms_path: Path = CONFIG_PATH,
    require_all_terms: bool = True,
) -> pd.DataFrame:
    """Return baseline cash flows in the same schema as nominal bonds."""
    terms = load_inflation_linked_terms(terms_path)
    market = bonds.loc[bonds["isincode"].astype(str).isin(terms)].copy()
    missing = sorted(set(terms) - set(market["isincode"].astype(str)))
    if missing and require_all_terms:
        raise ValueError(f"Inflation-linked ISINs absent from clean market universe: {missing}")
    terms = {isin: terms[isin] for isin in market["isincode"].astype(str) if isin in terms}
    provider = provider or load_baseline_index_provider()
    rows = []
    for row in market.itertuples(index=False):
        values = row._asdict()
        isincode = str(values["isincode"])
        valuation = pd.to_datetime(values["referencedate"], dayfirst=True).normalize()
        flows = pd.concat(
            [
                _purchase_cashflow(terms[isincode], pd.Series(values), provider),
                _future_cashflows(terms[isincode], provider, valuation),
            ],
            ignore_index=True,
        )
        flows.insert(0, "isincode", isincode)
        rows.append(flows)
    if not rows:
        return pd.DataFrame(columns=["isincode", "date", "l1", "l2", "l3"])
    return (
        pd.concat(rows, ignore_index=True).sort_values(["isincode", "date"]).reset_index(drop=True)
    )
