import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import yield_curve
from .utils import CONFIG_DIR

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


class Cf_engine:
    def __init__(self, liability):
        self.liability = liability

    def to_cf(self, index_provider=None):
        dates = pd.date_range(
            start=self.liability.start_date,
            end=self.liability.end_date - pd.DateOffset(months=1),
            freq="MS",
        )
        cf = np.zeros(len(dates))
        if self.liability.frequency in {"annual", "every_n_years"}:
            interval = 1 if self.liability.frequency == "annual" else self.liability.interval_years
            mask = dates.month == self.liability.start_date.month
            years_elapsed = dates.year - self.liability.start_date.year
            mask &= years_elapsed % interval == 0
            if self.liability.indexation:
                if index_provider is None:
                    raise ValueError(
                        "An index provider is required for scenario-indexed liabilities."
                    )
                terms = self.liability.indexation
                anchor = pd.Timestamp(terms.get("base_reference_date", self.liability.start_date))
                lag = int(terms.get("observation_lag_months", 0))
                base = index_provider.reference_level(terms["index_id"], anchor, lag)
                cf[mask] = [
                    self.liability.initial_cashflow
                    * index_provider.reference_level(terms["index_id"], date, lag)
                    / base
                    * self.liability.probability
                    for date in dates[mask]
                ]
            else:
                cf[mask] = (
                    self.liability.initial_cashflow
                    * (1 + self.liability.inflation_rate) ** years_elapsed[mask]
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
        if index_provider is None and any(e.liability.indexation for e in self.liabilities):
            from .inflation_linked_cashflows import load_selected_index_provider

            index_provider = load_selected_index_provider()
        cf_pairs = [e.to_cf(index_provider=index_provider) for e in self.liabilities]
        common_dates = pd.date_range(
            start=min(e.liability.start_date for e in self.liabilities),
            end=max(e.liability.end_date for e in self.liabilities) - pd.DateOffset(months=1),
            freq="MS",
        )
        all_dates = np.concatenate([d.to_numpy() for d, _ in cf_pairs])
        all_cf = np.concatenate([c for _, c in cf_pairs])

        idx = common_dates.get_indexer(all_dates)
        cf = np.zeros(len(common_dates))
        np.add.at(cf, idx, all_cf)
        return common_dates, cf

    def pv(self, index_provider=None):
        dates, cf = self.merge_liabilities(index_provider=index_provider)
        t = ((dates - self.valuation_date) / pd.Timedelta(days=365.25)).to_numpy()
        params = yield_curve.load_svensson_params(
            yield_curve.CSV_PATH, yield_curve.curves["All bonds"]
        )
        rates = yield_curve.svensson_yield(t, *params) / 100
        dcf = cf / (1 + rates) ** t
        return dcf, dcf.sum(), t, dates

    def duration(self, index_provider=None):
        dcf, pv, t, _ = self.pv(index_provider=index_provider)
        return np.sum(t * dcf) / pv


portfolio = LiabilityPortfolio(
    [Cf_engine(liability) for liability in load_liabilities()], dt.datetime.today()
)


def scenario_cashflows(scenario=None):
    """Return FOI-indexed liabilities under the active or explicitly selected scenario."""
    from .inflation_linked_cashflows import load_selected_index_provider

    provider = load_selected_index_provider(**({} if scenario is None else {"scenario": scenario}))
    dynamic_portfolio = LiabilityPortfolio(
        [Cf_engine(liability) for liability in load_liabilities()], dt.datetime.today()
    )
    return dynamic_portfolio.merge_liabilities(index_provider=provider)


if __name__ == "__main__":
    dcf, pv, t, dates = portfolio.pv()
    duration = np.sum(t * dcf) / pv
    print(f"PV:                {pv:>14,.2f}")
    print(f"Duration:          {duration:>14.3f}")
    for date, dcf_i in zip(dates, dcf):
        if dcf_i != 0:
            print(f"  {date.date()}   dcf: {dcf_i:>10,.2f}")
