import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import yield_curve
from utils import CONFIG_DIR

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


class Cf_engine:
    def __init__(self, liability):
        self.liability = liability

    def to_cf(self):
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

    def merge_liabilities(self):
        cf_pairs = [e.to_cf() for e in self.liabilities]
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

    def pv(self):
        dates, cf = self.merge_liabilities()
        t = ((dates - self.valuation_date) / pd.Timedelta(days=365.25)).to_numpy()
        params = yield_curve.load_svensson_params(
            yield_curve.CSV_PATH, yield_curve.curves["All bonds"]
        )
        rates = yield_curve.svensson_yield(t, *params) / 100
        dcf = cf / (1 + rates) ** t
        return dcf, dcf.sum(), t, dates

    def duration(self):
        dcf, pv, t, _ = self.pv()
        return np.sum(t * dcf) / pv


portfolio = LiabilityPortfolio(
    [Cf_engine(liability) for liability in load_liabilities()], dt.datetime.today()
)

if __name__ == "__main__":
    dcf, pv, t, dates = portfolio.pv()
    duration = np.sum(t * dcf) / pv
    print(f"PV:                {pv:>14,.2f}")
    print(f"Duration:          {duration:>14.3f}")
    for date, dcf_i in zip(dates, dcf):
        if dcf_i != 0:
            print(f"  {date.date()}   dcf: {dcf_i:>10,.2f}")
