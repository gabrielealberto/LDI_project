import unittest

import pandas as pd

from ldi_engine import (
    after_tax_cashflow_matrix,
    broker_commission,
    optimize_cashflow_matching,
)


class LDIOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.bonds = pd.DataFrame(
            {
                "isincode": ["EARLY", "LATE", "BROKEN"],
                "description": ["Early coupon", "Late coupon", "Invalid flow"],
                "price": [90.0, 100.0, 100.0],
            }
        )
        self.matrix = pd.DataFrame(
            {
                "2030-01": [1_000.0, 0.0, 1_000.0],
                "2030-02": [0.0, 1_000.0, -1.0],
            },
            index=pd.Index(["EARLY", "LATE", "BROKEN"], name="isincode"),
        )

    def test_earlier_cashflow_covers_later_liability(self):
        target = pd.DataFrame({"date": ["2030-02-01"], "cashflow": [1_000.0]})
        result = optimize_cashflow_matching(target, self.matrix, self.bonds)

        self.assertEqual(result["uncovered_eur"], 0.0)
        self.assertEqual(result["portfolio"].iloc[0]["isincode"], "EARLY")

    def test_cash_balance_can_carry_early_coupon(self):
        target = pd.DataFrame({"date": ["2030-01-01", "2030-02-01"], "cashflow": [500.0, 500.0]})
        result = optimize_cashflow_matching(target, self.matrix, self.bonds)

        self.assertEqual(result["uncovered_eur"], 0.0)
        self.assertEqual(result["portfolio"].iloc[0]["isincode"], "EARLY")
        self.assertTrue((result["cashflow_match"]["cash_balance_eur"] >= 0).all())

    def test_nominal_cap_limits_lots_and_reports_gap(self):
        target = pd.DataFrame({"date": ["2030-02-01"], "cashflow": [3_000.0]})
        result = optimize_cashflow_matching(
            target, self.matrix, self.bonds, max_nominal_per_bond=1_000
        )

        self.assertTrue((result["portfolio"]["lots"] <= 1).all())
        self.assertAlmostEqual(result["uncovered_eur"], 1_000.0, places=4)

    def test_tax_applies_to_coupon_not_principal_or_accrued(self):
        flows = pd.DataFrame(
            {
                "isincode": ["TEST", "TEST"],
                "date": pd.to_datetime(["2030-01-01", "2031-01-01"]),
                "l1": [-1_000.0, 1_000.0],
                "l2": [-10.0, 0.0],
                "l3": [0.0, 40.0],
            }
        )
        matrix = after_tax_cashflow_matrix(flows)

        self.assertEqual(matrix.loc["TEST", "2030-01"], -1_010.0)
        self.assertEqual(matrix.loc["TEST", "2031-01"], 1_035.0)

    def test_broker_commission_uses_rate_minimum_and_maximum(self):
        fees = broker_commission([0.0, 1_000.0, 10_000.0, 20_000.0])

        self.assertEqual(list(fees), [0.0, 2.95, 19.0, 19.0])

    def test_purchase_commission_is_included_in_cost_and_return(self):
        target = pd.DataFrame({"date": ["2030-01-01"], "cashflow": [1_000.0]})
        result = optimize_cashflow_matching(target, self.matrix, self.bonds)
        position = result["portfolio"].iloc[0]

        self.assertEqual(position["purchase_value_eur"], 900.0)
        self.assertEqual(position["purchase_commission_eur"], 2.95)
        self.assertEqual(position["cost_eur"], 902.95)
        self.assertEqual(result["purchase_commission_eur"], 2.95)
        self.assertEqual(result["sale_commission_eur"], 0.0)

    def test_single_known_issuer_cannot_bypass_concentration_limit(self):
        target = pd.DataFrame({"date": ["2030-01-01"], "cashflow": [1_000.0]})
        bonds = self.bonds.assign(issuercode="ONLY")
        result = optimize_cashflow_matching(target, self.matrix, bonds, max_nominal_per_bond=1_000)

        self.assertTrue(result["portfolio"].empty)
        self.assertAlmostEqual(result["uncovered_eur"], 1_000.0, places=4)

    def test_shortfall_branch_still_enforces_position_limit(self):
        target = pd.DataFrame({"date": ["2030-02-01"], "cashflow": [3_000.0]})
        result = optimize_cashflow_matching(
            target,
            self.matrix,
            self.bonds,
            max_nominal_per_bond=1_000,
            max_positions=0,
        )

        self.assertTrue(result["portfolio"].empty)
        self.assertAlmostEqual(result["uncovered_eur"], 3_000.0, places=4)

    def test_candidate_limit_no_longer_prunes_the_universe(self):
        target = pd.DataFrame({"date": ["2030-02-01"], "cashflow": [1_000.0]})
        result = optimize_cashflow_matching(target, self.matrix, self.bonds, candidate_limit=0)

        self.assertEqual(result["portfolio"].iloc[0]["isincode"], "EARLY")

    def test_weighted_maturity_uses_complete_position_cost(self):
        target = pd.DataFrame({"date": ["2030-02-01"], "cashflow": [3_000.0]})
        bonds = self.bonds.assign(
            redemptiondate=["01/01/2031", "01/01/2040", "01/01/2031"],
            referencedate="01/01/2030",
        )
        result = optimize_cashflow_matching(target, self.matrix, bonds, max_nominal_per_bond=2_000)
        portfolio = result["portfolio"]
        self.assertEqual(sorted(portfolio["lots"]), [1, 2])
        expected = (portfolio["maturity_years"] * portfolio["cost_eur"]).sum() / portfolio[
            "cost_eur"
        ].sum()

        self.assertAlmostEqual(result["weighted_average_maturity_years"], expected)


if __name__ == "__main__":
    unittest.main()
