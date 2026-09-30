import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from scripts.downloaders.yield_curve_downloader import ECBDownloader


class ECBDownloaderTests(unittest.TestCase):
    def _write_snapshot(self, downloader, archive_date):
        frame = pd.DataFrame(
            {
                "TIME_PERIOD": pd.Timestamp("2026-09-10"),
                "PARAMETER": downloader.PARAMETER_ORDER,
                "VALUE": [0.02, -0.01, 0.005, 0.002, 1.5, 8.0],
            }
        )
        path = downloader._archive_path(archive_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)

    def test_reuses_valid_curve_archived_today_without_downloading(self):
        with TemporaryDirectory() as directory:
            downloader = ECBDownloader(archive_dir=Path(directory))
            today = date(2026, 9, 11)
            self._write_snapshot(downloader, today)

            with patch.object(downloader, "_download_snapshot") as download:
                snapshot = downloader.run(today)

            self.assertFalse(snapshot.downloaded)
            self.assertFalse(snapshot.fallback)
            download.assert_not_called()

    def test_uses_recent_curve_when_a_new_download_fails(self):
        with TemporaryDirectory() as directory:
            downloader = ECBDownloader(archive_dir=Path(directory))
            today = date(2026, 9, 11)
            yesterday = today - timedelta(days=1)
            self._write_snapshot(downloader, yesterday)

            with patch.object(
                downloader, "_download_snapshot", side_effect=RuntimeError("down")
            ):
                snapshot = downloader.run(today)

            self.assertTrue(snapshot.fallback)
            self.assertEqual(snapshot.archive_date, yesterday)

    def test_rejects_stale_curve_fallback(self):
        with TemporaryDirectory() as directory:
            downloader = ECBDownloader(
                archive_dir=Path(directory), max_fallback_age_days=2
            )
            today = date(2026, 9, 11)
            self._write_snapshot(downloader, today - timedelta(days=3))

            with patch.object(
                downloader, "_download_snapshot", side_effect=RuntimeError("down")
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "beyond the 2-day fallback limit"
                ):
                    downloader.run(today)
