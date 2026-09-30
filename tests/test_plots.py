import tempfile
import unittest
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pandas as pd

from core.plots import (
    plot_inflation_baseline,
    plot_inflation_stress_funding,
    plot_inflation_stress_rates,
    plot_linkedin_summary,
)


class LinkedInPlotTests(unittest.TestCase):
    def test_creates_a_baseline_chart(self):
        dates = pd.date_range("2026-01-01", periods=36, freq="MS")
        baseline = pd.DataFrame(
            {
                "date": dates,
                "foi_xt_it": 100 * (1.02 ** (np.arange(36) / 12)),
                "hicp_xt_ea": 100 * (1.021 ** (np.arange(36) / 12)),
                "foi_yoy": np.linspace(0.025, 0.02, 36),
                "hicp_yoy": np.linspace(0.026, 0.02, 36),
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            baseline_path = temporary_path / "inflation_baseline.parquet"
            baseline.to_parquet(baseline_path, index=False)
            output = plot_inflation_baseline(temporary_path, baseline_path)
            image = mpimg.imread(output)

            self.assertTrue(output.is_file())
            self.assertEqual(output.name, "00_inflation_baseline.png")
            self.assertGreaterEqual(image.shape[0], 1_000)

    def test_creates_a_square_high_resolution_summary(self):
        result = {
            "portfolio": pd.DataFrame(
                {
                    "issuercode": ["GOV_IT", "GOV_FR"],
                    "cost_eur": [60_000.0, 40_000.0],
                }
            ),
            "cashflow_match": pd.DataFrame(
                {
                    "month": ["2030-01", "2030-02"],
                    "liability_eur": [50_000.0, 50_000.0],
                    "asset_cashflow_eur": [55_000.0, 45_000.0],
                }
            ),
            "uncovered_eur": 0.0,
            "annualized_return": 0.04,
            "weighted_average_maturity_years": 8.5,
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = plot_linkedin_summary(result, Path(temporary_directory))
            image = mpimg.imread(output)

            self.assertTrue(output.is_file())
            self.assertEqual(output.name, "04_linkedin_summary.png")
            self.assertEqual(image.shape[0], image.shape[1])
            self.assertGreaterEqual(image.shape[0], 1_200)

    def test_creates_stress_rate_and_funding_charts(self):
        dates = pd.date_range("2026-01-01", periods=24, freq="MS")
        baseline = pd.DataFrame({"date": dates, "foi_yoy": 0.02, "hicp_yoy": 0.02})
        stressed = baseline.copy()
        stressed["foi_yoy"] = np.linspace(0.02, 0.04, len(stressed))
        stressed["hicp_yoy"] = np.linspace(0.02, 0.035, len(stressed))
        report = {
            "scenario_paths": {
                "baseline": baseline,
                "inflation_upside_200bp": stressed,
            },
            "summary": pd.DataFrame(
                {
                    "scenario_id": ["baseline", "inflation_upside_200bp"],
                    "external_funding_eur": [0.0, 12_500.0],
                    "minimum_pre_funding_cash_balance_eur": [0.0, -12_500.0],
                    "deficit_months": [0, 2],
                }
            ),
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            rates = plot_inflation_stress_rates(report, output_dir)
            funding = plot_inflation_stress_funding(report, output_dir)

            self.assertEqual(rates.name, "05_inflation_stress_rates.png")
            self.assertEqual(funding.name, "06_inflation_stress_funding.png")
            self.assertTrue(rates.is_file())
            self.assertTrue(funding.is_file())
