import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from scripts.downloaders.bond_downloader import BondDownloader


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, page):
        self.page = page

    def get(self, *_args, **_kwargs):
        return _Response(self.page)


class BondDownloaderTests(unittest.TestCase):
    def _write_snapshot(self, downloader, archive_date):
        paths = downloader._archive_paths(archive_date)
        for key, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            columns = downloader.EXPORTS[key]["required_columns"]
            pd.DataFrame({column: ["value"] for column in columns}).to_parquet(path)

    def test_discovers_current_exports_without_assuming_table_layout(self):
        downloader = BondDownloader()
        downloader.session = _Session(
            """
            <div class='card'><a href='./data/export/end-of-day.csv'>Dati End of Day</a></div>
            <ul><li><a href='./data/export/bond-list.csv'>Elenco obbligazioni</a></li></ul>
            """
        )

        urls = downloader.discover_urls()

        self.assertEqual(
            urls["fd"],
            "https://www.simpletoolsforinvestors.eu/data/export/end-of-day.csv",
        )
        self.assertEqual(
            urls["bi"],
            "https://www.simpletoolsforinvestors.eu/data/export/bond-list.csv",
        )

    def test_schema_validation_rejects_a_swapped_export(self):
        with self.assertRaisesRegex(ValueError, "Dati End of Day.*pricetype"):
            BondDownloader.validate_schema(
                pd.DataFrame({"isincode": ["TEST"]}), BondDownloader.EXPORTS["fd"]
            )

    def test_reuses_valid_snapshot_archived_today_without_downloading(self):
        with TemporaryDirectory() as directory:
            downloader = BondDownloader(archive_dir=Path(directory))
            today = date(2026, 9, 11)
            self._write_snapshot(downloader, today)

            with patch.object(downloader, "_download_snapshot") as download:
                snapshot = downloader.run(today)

            self.assertFalse(snapshot.downloaded)
            self.assertFalse(snapshot.fallback)
            self.assertEqual(snapshot.archive_date, today)
            download.assert_not_called()

    def test_uses_recent_snapshot_when_a_new_download_fails(self):
        with TemporaryDirectory() as directory:
            downloader = BondDownloader(archive_dir=Path(directory))
            today = date(2026, 9, 11)
            yesterday = today - timedelta(days=1)
            self._write_snapshot(downloader, yesterday)

            with patch.object(
                downloader, "_download_snapshot", side_effect=RuntimeError("down")
            ):
                snapshot = downloader.run(today)

            self.assertTrue(snapshot.fallback)
            self.assertEqual(snapshot.archive_date, yesterday)

    def test_rejects_stale_fallback_snapshot(self):
        with TemporaryDirectory() as directory:
            downloader = BondDownloader(
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
