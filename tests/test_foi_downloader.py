import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.downloaders.download_foi_xt_it import (
    HISTORICAL_END,
    HISTORICAL_START,
    load_cached_pre_1996,
    parse_observations,
)


class HistoricalFoiCacheTests(unittest.TestCase):
    def _write_history(self, path, dates):
        pd.DataFrame(
            {
                "date": dates,
                "foi_xt_it": 100.0,
                "source_dataset": "ISTAT_Rivaluta_FOI_nt",
            }
        ).to_parquet(path, index=False)

    def test_reuses_complete_immutable_historical_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "foi.parquet"
            dates = pd.date_range(HISTORICAL_START, HISTORICAL_END, freq="MS")
            self._write_history(path, dates)

            cached = load_cached_pre_1996(path)

        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), 47)
        self.assertEqual(cached["date"].min(), HISTORICAL_START)
        self.assertEqual(cached["date"].max(), HISTORICAL_END)

    def test_rejects_incomplete_historical_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "foi.parquet"
            dates = pd.date_range(HISTORICAL_START, HISTORICAL_END, freq="MS").delete(4)
            self._write_history(path, dates)

            cached = load_cached_pre_1996(path)

        self.assertIsNone(cached)

    def test_parses_current_istat_sdmx_csv_format(self):
        payload = pd.DataFrame(
            {
                "FREQ": ["M", "M"],
                "REF_AREA": ["IT", "IT"],
                "DATA_TYPE": [101, 101],
                "MEASURE": [4, 4],
                "ECOICOP_2": ["00ST", "00ST"],
                "TIME_PERIOD": ["2026-07", "2026-08"],
                "OBS_VALUE": [103.1, 103.7],
                "OBS_STATUS": [None, None],
            }
        )

        parsed = parse_observations(payload, "flow", "2025=100", 1.0)

        self.assertEqual(parsed["date"].tolist(), [pd.Timestamp("2026-07-01"), pd.Timestamp("2026-08-01")])
        self.assertEqual(parsed["foi_xt_it"].tolist(), [103.1, 103.7])
