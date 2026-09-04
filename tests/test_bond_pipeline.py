import unittest

import pandas as pd

from core.bond_cash_flow_creator import create_all_cashflows, gross_ytm
from scripts.cleaners.bond_cleaner import clean_fd, liquid_bonds


class BondCashFlowTests(unittest.TestCase):
    def test_gross_ytm_for_one_year_cashflow(self):
        cashflows = pd.DataFrame(
            {
                "date": pd.to_datetime(["2030-01-01", "2031-01-01"]),
                "l1": [-1_000.0, 1_000.0],
                "l2": [0.0, 0.0],
                "l3": [0.0, 100.0],
            }
        )

        self.assertAlmostEqual(gross_ytm(cashflows), 0.1, places=12)

    def test_creates_cashflows_for_every_bond(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["A", "B"],
                "referencedate": ["01/01/2030", "01/01/2030"],
                "redemptiondate": ["01/01/2031", "01/01/2032"],
                "couponperiodicity": [0, 0],
                "currentcouponrate": [0.0, 0.0],
                "couponmonths": [None, None],
                "price": [90.0, 95.0],
            }
        )

        cashflows = create_all_cashflows(bonds)

        self.assertEqual(cashflows.groupby("isincode").size().to_dict(), {"A": 2, "B": 2})


class BondCleanerTests(unittest.TestCase):
    def test_rejects_non_numeric_and_oversized_minimum_lots(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["VALID", "INVALID", "LARGE"],
                "currencycode": ["EUR"] * 3,
                "issuercode": ["GOV_IT"] * 3,
                "ratingsp": ["A"] * 3,
                "ratingmoodys": [None] * 3,
                "minimumlot": [1_000, "invalid", 2_000],
                "pricetype": ["LP"] * 3,
                "volume": [20_000] * 3,
                "redemptiondate": ["01/01/2032"] * 3,
                "referencedate": ["01/01/2030"] * 3,
                "ratingfitch": [None] * 3,
            }
        )

        cleaned, report = clean_fd(bonds)

        self.assertEqual(cleaned["isincode"].tolist(), ["VALID"])
        self.assertEqual(report["minimumlot"], 1)

    def test_requires_a_traded_price_and_sufficient_daily_volume(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["LIQUID", "REFERENCE", "THIN", "INVALID"],
                "pricetype": [" lp ", "RP", "LP", "LP"],
                "volume": [20_000, 100_000, 19_999, "invalid"],
            }
        )

        cleaned = liquid_bonds(bonds, min_daily_volume=20_000)

        self.assertEqual(cleaned["isincode"].tolist(), ["LIQUID"])

    def test_rejects_a_negative_liquidity_threshold(self):
        bonds = pd.DataFrame({"pricetype": ["LP"], "volume": [20_000]})

        with self.assertRaisesRegex(ValueError, "finite non-negative"):
            liquid_bonds(bonds, min_daily_volume=-1)

    def test_requires_the_upstream_liquidity_columns(self):
        with self.assertRaisesRegex(ValueError, "pricetype"):
            liquid_bonds(pd.DataFrame({"volume": [20_000]}))


if __name__ == "__main__":
    unittest.main()
