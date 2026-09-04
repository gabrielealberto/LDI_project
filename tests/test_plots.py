import tempfile
import unittest
from pathlib import Path

import matplotlib.image as mpimg
import pandas as pd

from core.plots import plot_linkedin_summary


class LinkedInPlotTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
