import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from core.inflation_stress import (
    InflationShock,
    build_stressed_baseline,
    load_inflation_stresses,
)
from core.inflation_stress_testing import _monthly_after_tax_cashflows, replay_frozen_cashflows


class InflationStressTests(unittest.TestCase):
    def setUp(self):
        history_dates = pd.date_range("2024-01-01", periods=24, freq="MS")
        baseline_dates = pd.date_range("2026-01-01", periods=36, freq="MS")
        history_levels = 100 * np.exp(np.arange(len(history_dates)) * 0.02 / 12)
        baseline_levels = history_levels[-1] * np.exp(
            np.arange(1, len(baseline_dates) + 1) * 0.02 / 12
        )
        self.history = pd.DataFrame(
            {
                "date": history_dates,
                "foi_xt_it": history_levels,
                "hicp_xt_ea": history_levels,
            }
        )
        self.baseline = pd.DataFrame(
            {
                "date": baseline_dates,
                "foi_xt_it": baseline_levels,
                "hicp_xt_ea": baseline_levels,
                "foi_hicp_log_spread": 0.0,
                "model_version": "anchor_decay_v1",
            }
        )

    def test_zero_shock_preserves_the_baseline_levels_exactly(self):
        stressed = build_stressed_baseline(
            self.baseline,
            self.history,
            InflationShock("zero", pd.Timestamp("2026-06-01")),
        )

        np.testing.assert_array_equal(
            stressed[["foi_xt_it", "hicp_xt_ea"]].to_numpy(),
            self.baseline[["foi_xt_it", "hicp_xt_ea"]].to_numpy(),
        )

    def test_common_shock_preserves_pre_shock_levels_and_lifts_both_indices(self):
        scenario = InflationShock(
            "up", pd.Timestamp("2026-06-01"), common_annual_shock_bp=200
        )
        stressed = build_stressed_baseline(self.baseline, self.history, scenario)
        before = stressed["date"] < scenario.start_date

        np.testing.assert_array_equal(
            stressed.loc[before, ["foi_xt_it", "hicp_xt_ea"]].to_numpy(),
            self.baseline.loc[before, ["foi_xt_it", "hicp_xt_ea"]].to_numpy(),
        )
        self.assertGreater(stressed["foi_xt_it"].iloc[-1], self.baseline["foi_xt_it"].iloc[-1])
        self.assertGreater(stressed["hicp_xt_ea"].iloc[-1], self.baseline["hicp_xt_ea"].iloc[-1])

    def test_basis_point_shock_is_converted_to_a_monthly_log_rate(self):
        scenario = InflationShock(
            "rate", pd.Timestamp("2026-01-01"), common_annual_shock_bp=120, ramp_months=1
        )
        stressed = build_stressed_baseline(self.baseline, self.history, scenario)
        expected_ratio = np.exp(0.012 / 12)

        self.assertAlmostEqual(
            stressed["hicp_xt_ea"].iloc[0] / self.baseline["hicp_xt_ea"].iloc[0],
            expected_ratio,
            places=12,
        )
        self.assertAlmostEqual(
            stressed["hicp_xt_ea"].iloc[1] / self.baseline["hicp_xt_ea"].iloc[1],
            expected_ratio**2,
            places=12,
        )

    def test_spread_shock_changes_foi_relative_to_hicp(self):
        stressed = build_stressed_baseline(
            self.baseline,
            self.history,
            InflationShock(
                "spread", pd.Timestamp("2026-01-01"), foi_hicp_spread_shock_bp=100
            ),
        )

        self.assertGreater(
            stressed["foi_xt_it"].iloc[-1] / stressed["hicp_xt_ea"].iloc[-1], 1.0
        )
        np.testing.assert_array_equal(
            stressed["hicp_xt_ea"].to_numpy(), self.baseline["hicp_xt_ea"].to_numpy()
        )

    def test_configuration_rejects_duplicate_scenario_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenarios.json"
            path.write_text(
                '[{"scenario_id":"duplicate","start_date":"2026-01-01"},'
                '{"scenario_id":"duplicate","start_date":"2026-02-01"}]',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_inflation_stresses(path)

    def test_replay_reports_the_exact_external_cash_needed(self):
        monthly = replay_frozen_cashflows(
            pd.Series([100.0], index=pd.Index(["2027-01"])),
            pd.to_datetime(["2027-01-01", "2027-02-01"]),
            [100.0, 200.0],
        )

        self.assertEqual(monthly["external_cash_eur"].tolist(), [0.0, 200.0])
        self.assertTrue((monthly["cash_balance_eur"] >= 0).all())

    def test_asset_replay_uses_the_optimizer_monthly_netting_convention(self):
        cashflows = pd.DataFrame(
            {
                "isincode": ["TEST", "TEST"],
                "date": pd.to_datetime(["2030-01-15", "2030-01-15"]),
                "l1": [-1_000.0, 0.0],
                "l2": [0.0, 0.0],
                "l3": [0.0, 100.0],
            }
        )
        monthly = _monthly_after_tax_cashflows(cashflows, pd.Series([1], index=["TEST"]))

        self.assertEqual(monthly.to_dict(), {"2030-01": 0.0})
