import unittest

import numpy as np
import pandas as pd

from core.inflation_baseline import BaselineConfig, build_baseline


class InflationBaselineTests(unittest.TestCase):
    def _history(self):
        dates = pd.date_range("2000-01-01", periods=300, freq="MS")
        seasonal = np.array([0.001, -0.0005, 0.0003, 0.0002, 0.0001, 0.0] * 2)
        hicp_changes = np.log1p(0.024) / 12 + np.resize(seasonal, len(dates))
        foi_changes = np.log1p(0.026) / 12 + np.resize(seasonal * 1.1, len(dates))
        return pd.DataFrame(
            {
                "date": dates,
                "foi_xt_it": 100 * np.exp(np.cumsum(foi_changes)),
                "hicp_xt_ea": 100 * np.exp(np.cumsum(hicp_changes)),
            }
        )

    def test_builds_one_positive_monthly_baseline_without_scenario_metadata(self):
        history = self._history()
        baseline = build_baseline(history, pd.Timestamp("2030-12-01"))

        self.assertEqual(baseline["date"].iloc[0], pd.Timestamp("2025-01-01"))
        self.assertEqual(baseline["date"].iloc[-1], pd.Timestamp("2030-12-01"))
        self.assertFalse(
            {"scenario", "probability", "selection_percentile"} & set(baseline)
        )
        self.assertTrue((baseline[["foi_xt_it", "hicp_xt_ea"]] > 0).all().all())
        self.assertEqual(baseline["model_version"].nunique(), 1)

    def test_long_horizon_indices_converge_to_the_common_target(self):
        history = self._history()
        config = BaselineConfig(convergence_half_life_months=12)
        baseline = build_baseline(history, pd.Timestamp("2040-12-01"), config)

        self.assertAlmostEqual(baseline["hicp_yoy"].iloc[-1], 0.02, places=4)
        self.assertAlmostEqual(baseline["foi_yoy"].iloc[-1], 0.02, places=4)

    def test_reported_yoy_is_the_change_implied_by_the_index_levels(self):
        history = self._history()
        baseline = build_baseline(history, pd.Timestamp("2028-12-01"))
        combined = pd.concat(
            [
                history[["date", "hicp_xt_ea"]].tail(12),
                baseline[["date", "hicp_xt_ea"]],
            ],
            ignore_index=True,
        )
        expected = combined["hicp_xt_ea"].pct_change(12).iloc[12:].to_numpy()

        np.testing.assert_allclose(baseline["hicp_yoy"].to_numpy(), expected)
