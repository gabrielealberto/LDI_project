import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.inflation_linked_cashflows import build_index_provider


class InflationProviderContractTests(unittest.TestCase):
    def _write_series(self, path, column, values):
        pd.DataFrame(
            {
                "date": pd.date_range("2020-01-01", periods=len(values), freq="MS"),
                column: values,
            }
        ).to_parquet(path, index=False)

    def test_reference_level_interpolates_between_lagged_months(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foi = root / "foi.parquet"
            hicp = root / "hicp.parquet"
            self._write_series(foi, "foi_xt_it", range(100, 112))
            self._write_series(hicp, "hicp_xt_ea", range(200, 212))
            baseline = pd.DataFrame(
                {
                    "date": pd.date_range("2021-01-01", periods=2, freq="MS"),
                    "foi_xt_it": [112.0, 113.0],
                    "hicp_xt_ea": [212.0, 213.0],
                }
            )
            provider = build_index_provider(baseline, foi, hicp)

            value = provider.reference_level(
                "FOI_XT_IT", pd.Timestamp("2020-06-15"), lag_months=3
            )

        self.assertAlmostEqual(value, 102.4666666667, places=8)

    def test_rejects_history_forecast_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foi = root / "foi.parquet"
            hicp = root / "hicp.parquet"
            self._write_series(foi, "foi_xt_it", range(100, 112))
            self._write_series(hicp, "hicp_xt_ea", range(200, 212))
            baseline = pd.DataFrame(
                {
                    "date": [pd.Timestamp("2020-12-01")],
                    "foi_xt_it": [112.0],
                    "hicp_xt_ea": [212.0],
                }
            )

            with self.assertRaisesRegex(ValueError, "overlap"):
                build_index_provider(baseline, foi, hicp)

    def test_rejects_gaps_between_history_and_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foi = root / "foi.parquet"
            hicp = root / "hicp.parquet"
            self._write_series(foi, "foi_xt_it", range(100, 112))
            self._write_series(hicp, "hicp_xt_ea", range(200, 212))
            baseline = pd.DataFrame(
                {
                    "date": [pd.Timestamp("2021-02-01")],
                    "foi_xt_it": [113.0],
                    "hicp_xt_ea": [213.0],
                }
            )

            with self.assertRaisesRegex(ValueError, "monthly gap"):
                build_index_provider(baseline, foi, hicp)
