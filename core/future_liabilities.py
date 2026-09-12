import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import yield_curve
from .utils import (
    CONFIG_DIR,
    LIABILITY_END_DATE_INCLUSIVE,
    LIABILITY_PAYMENT_TIMING,
)

LIABILITIES_PATH = CONFIG_DIR / "liabilities.json"


class Liability:
    def __init__(
        self,
        name,
        category,
        start_date,
        end_date,
        initial_cashflow,
        frequency,
        inflation_rate,
        probability=1,
        flexibility=0,
        interval_years=None,
        indexation=None,
    ):
        self.name = name
        self.category = category
        self.start_date = dt.datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = dt.datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_cashflow = initial_cashflow
        self.frequency = frequency
        self.inflation_rate = inflation_rate
        self.probability = probability
        self.flexibility = flexibility
        self.interval_years = interval_years
        self.indexation = indexation


def _payment_dates(
    liability,
    timing=LIABILITY_PAYMENT_TIMING,
    end_date_inclusive=LIABILITY_END_DATE_INCLUSIVE,
):
    """Build contractual dates from the documented liability schedule policy."""
    if timing not in {"period_start", "period_end"}:
        raise ValueError("Liability payment timing must be period_start or period_end.")
    start = pd.Timestamp(liability.start_date).replace(day=1)
    end = pd.Timestamp(liability.end_date).replace(day=1)
    if end < start:
        raise ValueError("Liability end_date must not precede start_date.")
    if not end_date_inclusive:
        end -= pd.DateOffset(months=1)

    interval = 1 if liability.frequency == "annual" else liability.interval_years
    if liability.frequency not in {"annual", "every_n_years"}:
        return pd.DatetimeIndex([])
    if interval is None or int(interval) != interval or interval <= 0:
        raise ValueError("Liability interval_years must be a positive integer.")

    periods = pd.date_range(start=start, end=end, freq="MS")
    periods = periods[
        ((periods.year - start.year) % int(interval) == 0)
        & (periods.month == start.month)
    ]
    if timing == "period_start":
        return periods
    return pd.DatetimeIndex(
        [
            period + pd.DateOffset(years=int(interval)) - pd.Timedelta(days=1)
            for period in periods
        ]
    )


class Cf_engine:
    def __init__(self, liability):
        self.liability = liability

    def to_cf(self, index_provider=None):
        dates = _payment_dates(self.liability)
        cf = np.zeros(len(dates))
        if self.liability.frequency in {"annual", "every_n_years"}:
            years_elapsed = dates.year - self.liability.start_date.year
            if self.liability.indexation:
                if index_provider is None:
                    raise ValueError(
                        "An index provider is required for inflation-indexed liabilities."
                    )
                terms = self.liability.indexation
                anchor = pd.Timestamp(
                    terms.get("base_reference_date", self.liability.start_date)
                )
                lag = int(terms.get("observation_lag_months", 0))
                base = index_provider.reference_level(terms["index_id"], anchor, lag)
                cf[:] = [
                    self.liability.initial_cashflow
                    * index_provider.reference_level(terms["index_id"], date, lag)
                    / base
                    * self.liability.probability
                    for date in dates
                ]
            else:
                cf[:] = (
                    self.liability.initial_cashflow
                    * (1 + self.liability.inflation_rate) ** years_elapsed
                    * self.liability.probability
                )
        return dates, cf


def load_liabilities(path=LIABILITIES_PATH):
    """Load the liability definitions from the project JSON configuration."""
    with Path(path).open(encoding="utf-8") as file:
        return [Liability(**definition) for definition in json.load(file)]


class LiabilityPortfolio:
    def __init__(self, liabilities, valuation_date):
        self.liabilities = liabilities
        self.valuation_date = valuation_date

    def merge_liabilities(self, index_provider=None):
        if index_provider is None and any(
            e.liability.indexation for e in self.liabilities
        ):
            from .inflation_linked_cashflows import load_baseline_index_provider

            index_provider = load_baseline_index_provider()
        cf_pairs = [e.to_cf(index_provider=index_provider) for e in self.liabilities]
        all_dates = np.concatenate([d.to_numpy() for d, _ in cf_pairs])
        # The optimiser works on monthly buckets. Keep the contractual dates
        # above for indexation, then map each payment to its calendar month.
        all_months = pd.DatetimeIndex(all_dates).to_period("M").to_timestamp()
        common_dates = pd.date_range(all_months.min(), all_months.max(), freq="MS")
        all_cf = np.concatenate([c for _, c in cf_pairs])

        idx = common_dates.get_indexer(all_months)
        cf = np.zeros(len(common_dates))
        np.add.at(cf, idx, all_cf)
        return common_dates, cf

    def pv(self, index_provider=None):
        dates, cf = self.merge_liabilities(index_provider=index_provider)
        t = ((dates - self.valuation_date) / pd.Timedelta(days=365.25)).to_numpy()
        params = yield_curve.load_svensson_params(
            yield_curve.latest_curve_path(), yield_curve.curves["All bonds"]
        )
        rates = yield_curve.svensson_yield(t, *params) / 100
        dcf = cf / (1 + rates) ** t
        return dcf, dcf.sum(), t, dates

    def duration(self, index_provider=None):
        dcf, pv, t, _ = self.pv(index_provider=index_provider)
        return np.sum(t * dcf) / pv


def cashflows_for_provider(index_provider):
    """Return all configured liabilities using one explicit index provider."""
    dynamic_portfolio = LiabilityPortfolio(
        [Cf_engine(liability) for liability in load_liabilities()], dt.datetime.today()
    )
    return dynamic_portfolio.merge_liabilities(index_provider=index_provider)


def baseline_cashflows():
    """Return FOI-indexed liabilities under the sole forecast baseline."""
    from .inflation_linked_cashflows import load_baseline_index_provider

    return cashflows_for_provider(load_baseline_index_provider())
