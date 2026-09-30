import tempfile
import unittest
from pathlib import Path

import pandas as pd

import core.pipeline as pipeline


class PipelineCacheContractTests(unittest.TestCase):
    def _write_index(self, path, column, dates):
        pd.DataFrame({"date": dates, column: range(100, 100 + len(dates))}).to_parquet(
            path, index=False
        )

    def test_monthly_cache_accepts_recent_gap_free_positive_data(self):
        latest = pd.Timestamp.today().to_period("M").to_timestamp()
        dates = pd.date_range(latest - pd.DateOffset(months=2), latest, freq="MS")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.parquet"
            self._write_index(path, "value", dates)

            usable = pipeline._usable_cached_monthly_index(
                path, "value", max_age_months=2
            )

        self.assertTrue(usable)

    def test_monthly_cache_rejects_a_gap(self):
        latest = pd.Timestamp.today().to_period("M").to_timestamp()
        dates = [latest - pd.DateOffset(months=2), latest]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.parquet"
            self._write_index(path, "value", dates)

            usable = pipeline._usable_cached_monthly_index(
                path, "value", max_age_months=2
            )

        self.assertFalse(usable)

    def test_require_outputs_names_every_missing_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / "a", Path(directory) / "b"]

            with self.assertRaisesRegex(RuntimeError, "a.*b"):
                pipeline._require_outputs(paths, "test-stage")
