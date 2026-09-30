import unittest

import pandas as pd

from core.bond_cash_flow_creator import (
    apply_cashflow_overrides,
    calendar,
    create_cashflows,
    effective_frequency,
    monthly_cashflow_matrix,
)


class BondCashflowEdgeTests(unittest.TestCase):
    def test_zero_coupon_has_only_purchase_and_redemption(self):
        bond = {
            "isincode": "ZERO",
            "referencedate": "01/01/2030",
            "redemptiondate": "01/01/2032",
            "couponperiodicity": 0,
            "currentcouponrate": 0.0,
            "couponmonths": None,
            "price": 98.0,
        }

        cashflows = create_cashflows(bond)

        self.assertEqual(len(cashflows), 2)
        self.assertEqual(
            cashflows["date"].dt.strftime("%Y-%m-%d").tolist(),
            ["2030-01-01", "2032-01-01"],
        )
        self.assertEqual(cashflows.iloc[0]["l1"], -980.0)
        self.assertEqual(cashflows.iloc[1]["l1"], 1_000.0)

    def test_coupon_calendar_includes_last_realised_coupon_and_future_dates(self):
        dates = calendar("3,9", 2, "15/09/2031", "20/04/2030")

        self.assertEqual(
            dates.strftime("%Y-%m-%d").tolist(),
            ["2030-03-15", "2030-09-15", "2031-03-15", "2031-09-15"],
        )

    def test_effective_frequency_falls_back_to_annual_for_nonzero_coupon(self):
        self.assertEqual(effective_frequency(0, 0.01), 1)
        self.assertEqual(effective_frequency(2, 0.01), 2)
        self.assertEqual(effective_frequency(2, 0.0), 0)

    def test_override_replaces_only_future_rows_and_keeps_purchase_flow(self):
        bonds = pd.DataFrame({"isincode": ["A"], "referencedate": ["01/01/2030"]})
        original = pd.DataFrame(
            {
                "isincode": ["A", "A"],
                "date": pd.to_datetime(["2030-01-01", "2031-01-01"]),
                "l1": [-980.0, 1_000.0],
                "l2": [0.0, 0.0],
                "l3": [0.0, 0.0],
            }
        )
        overrides = pd.DataFrame(
            {
                "isincode": ["A", "A"],
                "date": pd.to_datetime(["2030-01-01", "2031-01-01"]),
                "l1": [-999.0, 1_000.0],
                "l2": [0.0, 0.0],
                "l3": [0.0, 25.0],
            }
        )

        result = apply_cashflow_overrides(original, overrides, bonds)

        self.assertEqual(result.iloc[0]["l1"], -980.0)
        self.assertEqual(result.iloc[1]["l1"], 1_000.0)
        self.assertEqual(result.iloc[1]["l3"], 25.0)

    def test_monthly_matrix_nets_same_isin_and_month(self):
        cashflows = pd.DataFrame(
            {
                "isincode": ["A", "A", "A"],
                "date": pd.to_datetime(["2030-01-02", "2030-01-20", "2030-02-01"]),
                "l1": [-1_000.0, 0.0, 1_000.0],
                "l2": [0.0, 0.0, 0.0],
                "l3": [0.0, 50.0, 0.0],
            }
        )

        matrix = monthly_cashflow_matrix(cashflows)

        self.assertEqual(matrix.loc["A", "2030-01"], -950.0)
        self.assertEqual(matrix.loc["A", "2030-02"], 1_000.0)
