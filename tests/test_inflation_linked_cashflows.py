import unittest

import pandas as pd

from core.inflation_linked_cashflows import ScenarioIndexProvider, _future_cashflows


def provider(values):
    months = pd.date_range("2024-01-01", "2026-03-01", freq="MS")
    series = pd.Series(100.0, index=months)
    for month, value in values.items():
        series.loc[pd.Timestamp(month)] = value
    return ScenarioIndexProvider({"HICP_XT_EA": series, "FOI_XT_IT": series.copy()}, "test")


class InflationLinkedCashflowTests(unittest.TestCase):
    def test_daily_reference_index_uses_three_month_lag_and_linear_interpolation(self):
        indexes = provider({"2025-04-01": 100.0, "2025-05-01": 131.0})

        self.assertAlmostEqual(
            indexes.reference_level("HICP_XT_EA", pd.Timestamp("2025-07-16")), 115.0
        )

    def test_btpei_redeems_nominal_and_taxable_inflation_uplift(self):
        indexes = provider(
            {
                "2024-10-01": 100.0,
                "2024-11-01": 100.0,
                "2025-04-01": 110.0,
                "2025-05-01": 110.0,
                "2025-10-01": 121.0,
                "2025-11-01": 121.0,
            }
        )
        term = {
            "type": "btpei",
            "nominal_per_lot": 1000,
            "issue_date": "2025-01-15",
            "maturity_date": "2026-01-15",
            "real_annual_coupon_rate": 0.02,
            "cashflow_schedule": {"frequency_per_year": 2, "first_coupon_date": "2025-07-15"},
            "indexation": {
                "index_id": "HICP_XT_EA",
                "observation_lag_months": 3,
                "base_reference_date": "2025-01-15",
            },
        }

        flows = _future_cashflows(term, indexes, pd.Timestamp("2025-01-01"))

        self.assertAlmostEqual(flows.iloc[0].l3, 11.0)
        self.assertAlmostEqual(flows.iloc[1].l1, 1000.0)
        self.assertAlmostEqual(flows.iloc[1].l3, 10 * 1.21 + 210.0)

    def test_btp_italia_revaluation_uses_a_cumulative_high_water_mark(self):
        indexes = provider(
            {
                "2024-10-01": 100.0,
                "2024-11-01": 100.0,
                "2025-04-01": 110.0,
                "2025-05-01": 110.0,
                "2025-10-01": 105.0,
                "2025-11-01": 105.0,
            }
        )
        term = {
            "type": "btp_italia",
            "nominal_per_lot": 1000,
            "issue_date": "2025-01-15",
            "maturity_date": "2026-01-15",
            "real_annual_coupon_rate": 0.02,
            "cashflow_schedule": {"frequency_per_year": 2, "first_coupon_date": "2025-07-15"},
            "indexation": {"index_id": "FOI_XT_IT", "observation_lag_months": 3},
        }

        flows = _future_cashflows(term, indexes, pd.Timestamp("2025-01-01"))

        self.assertAlmostEqual(flows.iloc[0].l3, 111.0)
        self.assertAlmostEqual(flows.iloc[1].l3, 10.0)
        self.assertEqual(flows.iloc[1].l1, 1000.0)


if __name__ == "__main__":
    unittest.main()
