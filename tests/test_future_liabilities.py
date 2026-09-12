import json
import tempfile
import unittest
from pathlib import Path

from core.future_liabilities import (
    Cf_engine,
    Liability,
    _payment_dates,
    load_liabilities,
)


class LiabilityConfigurationTests(unittest.TestCase):
    def test_loads_liability_definitions_from_json(self):
        definition = [
            {
                "name": "Test",
                "category": "test",
                "start_date": "2030-01-01",
                "end_date": "2031-01-01",
                "initial_cashflow": 1000,
                "frequency": "annual",
                "inflation_rate": 0.0,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "liabilities.json"
            path.write_text(json.dumps(definition), encoding="utf-8")
            liabilities = load_liabilities(path)

        self.assertEqual(liabilities[0].name, "Test")
        self.assertEqual(liabilities[0].initial_cashflow, 1000)

    def test_generates_a_payment_every_n_years(self):
        liability = Liability(
            name="Auto",
            category="capex",
            start_date="2028-01-01",
            end_date="2040-01-01",
            initial_cashflow=5000,
            frequency="every_n_years",
            interval_years=7,
            inflation_rate=0.025,
        )
        dates, cashflows = Cf_engine(liability).to_cf()
        payments = list(
            zip(dates[cashflows > 0].strftime("%Y-%m-%d"), cashflows[cashflows > 0])
        )

        self.assertEqual(
            payments, [("2028-01-01", 5000.0), ("2035-01-01", 5000.0 * 1.025**7)]
        )

    def test_includes_the_configured_last_annual_payment(self):
        liability = Liability(
            name="Inclusive",
            category="liability",
            start_date="2028-01-01",
            end_date="2030-01-01",
            initial_cashflow=1000,
            frequency="annual",
            inflation_rate=0.0,
        )

        dates, cashflows = Cf_engine(liability).to_cf()

        self.assertEqual(
            list(dates[cashflows > 0].strftime("%Y-%m-%d")),
            ["2028-01-01", "2029-01-01", "2030-01-01"],
        )

    def test_period_end_policy_preserves_the_month_for_matching(self):
        liability = Liability(
            name="Year end",
            category="liability",
            start_date="2028-01-01",
            end_date="2030-01-01",
            initial_cashflow=1000,
            frequency="annual",
            inflation_rate=0.0,
        )

        dates = _payment_dates(liability, timing="period_end")

        self.assertEqual(
            list(dates.strftime("%Y-%m-%d")),
            ["2028-12-31", "2029-12-31", "2030-12-31"],
        )
