from dataclasses import dataclass
from datetime import date
from io import BytesIO
import logging
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from core.ingestion_support import atomic_to_parquet, retry_session


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
BOND_ARCHIVE_DIR = RAW_DIR / "bonds"
MAX_FALLBACK_AGE_DAYS = 5


@dataclass(frozen=True)
class BondSnapshot:
    """One immutable pair of bond-source files archived on a calendar day."""

    archive_date: date
    fd_path: Path
    bi_path: Path
    downloaded: bool
    fallback: bool
    urls: dict[str, str] | None = None


class BondDownloader:
    DOCUMENTS_URL = "https://www.simpletoolsforinvestors.eu/documentivari.php"
    EXPORTS = {
        "bi": {
            "label": "Elenco obbligazioni",
            "fallback_url": "https://www.simpletoolsforinvestors.eu/data/export/F029B4ABADACFD88CB5D435F80AFED8A.csv",
            "required_columns": {
                "isincode",
                "description",
                "firstdate",
                "issuedate",
                "redemptiondate",
                "issueprice",
                "redemptionprice",
            },
        },
        "fd": {
            "label": "Dati End of Day",
            "fallback_url": "https://www.simpletoolsforinvestors.eu/data/export/A74B57BD582CD0A297800FD16C2D5606.csv",
            "required_columns": {
                "isincode",
                "currencycode",
                "issuercode",
                "ratingsp",
                "ratingmoodys",
                "ratingfitch",
                "minimumlot",
                "referencedate",
                "redemptiondate",
                "pricetype",
                "volume",
            },
        },
    }

    def __init__(
        self,
        timeout=30,
        archive_dir=BOND_ARCHIVE_DIR,
        max_fallback_age_days=MAX_FALLBACK_AGE_DAYS,
    ):
        self.timeout = timeout
        self.archive_dir = Path(archive_dir)
        self.max_fallback_age_days = max_fallback_age_days
        self.session = retry_session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "text/csv,text/html,*/*;q=0.8",
            }
        )

    def discover_urls(self):
        response = self.session.get(self.DOCUMENTS_URL, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        urls = {}

        # The site no longer exposes the exports as a two-column table. Match
        # the visible anchor text directly so the discovery survives layout-only
        # changes such as cards, lists, or Bootstrap components.
        for link in soup.find_all("a", href=True):
            description = " ".join(link.stripped_strings)
            for key, export in self.EXPORTS.items():
                if description.casefold() == export["label"].casefold():
                    urls[key] = urljoin(self.DOCUMENTS_URL, link["href"])

        return urls

    def download_csv(self, url):
        response = self.session.get(
            url,
            headers={"Referer": self.DOCUMENTS_URL},
            timeout=self.timeout,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "csv" not in content_type.lower() and not url.lower().endswith(".csv"):
            raise ValueError(
                f"Unexpected response from {url}: content-type {content_type!r}"
            )

        return response.content

    def read_csv(self, content):
        df = pd.read_csv(
            BytesIO(content),
            sep=";",
            decimal=",",
            low_memory=False,
        )
        return df.loc[:, ~df.columns.str.startswith("Unnamed:")]

    @staticmethod
    def validate_schema(frame, export):
        """Reject a swapped or structurally changed export before publishing it."""
        missing = export["required_columns"] - set(frame.columns)
        if missing:
            raise ValueError(
                f"Unexpected {export['label']!r} export: missing columns {sorted(missing)}"
            )

    def _archive_paths(self, archive_date: date) -> dict[str, Path]:
        stamp = archive_date.strftime("%Y%m%d")
        return {
            "fd": self.archive_dir / f"fd_{stamp}.parquet",
            "bi": self.archive_dir / f"bi_{stamp}.parquet",
        }

    def _valid_snapshot(self, archive_date: date) -> BondSnapshot | None:
        paths = self._archive_paths(archive_date)
        if not all(path.is_file() for path in paths.values()):
            return None
        try:
            for key, path in paths.items():
                columns = list(self.EXPORTS[key]["required_columns"])
                frame = pd.read_parquet(path, columns=columns)
                if frame.empty:
                    return None
                self.validate_schema(frame, self.EXPORTS[key])
        except (OSError, ValueError, KeyError):
            return None
        return BondSnapshot(
            archive_date=archive_date,
            fd_path=paths["fd"],
            bi_path=paths["bi"],
            downloaded=False,
            fallback=False,
        )

    def _latest_valid_snapshot(self, before: date) -> BondSnapshot | None:
        candidates = []
        for path in self.archive_dir.glob("fd_????????.parquet"):
            try:
                stamp = path.stem.removeprefix("fd_")
                candidates.append(
                    date.fromisoformat(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}")
                )
            except ValueError:
                continue
        for archive_date in sorted(
            (value for value in candidates if value < before), reverse=True
        ):
            snapshot = self._valid_snapshot(archive_date)
            if snapshot is not None:
                return snapshot
        return None

    def _download_snapshot(self, archive_date: date) -> BondSnapshot:
        discovered_urls = self.discover_urls()
        paths = self._archive_paths(archive_date)
        urls = {}

        for key, export in self.EXPORTS.items():
            url = discovered_urls.get(key, export["fallback_url"])
            if key not in discovered_urls:
                logging.warning(
                    "SimpleTools export %s was not discovered; using configured fallback URL.",
                    export["label"],
                )
            df = self.read_csv(self.download_csv(url))
            self.validate_schema(df, export)
            atomic_to_parquet(df, paths[key])
            urls[key] = url

        return BondSnapshot(
            archive_date=archive_date,
            fd_path=paths["fd"],
            bi_path=paths["bi"],
            downloaded=True,
            fallback=False,
            urls=urls,
        )

    def run(self, archive_date: date | None = None) -> BondSnapshot:
        """Reuse today's snapshot, download it once, or use a recent fallback."""
        archive_date = archive_date or date.today()
        current = self._valid_snapshot(archive_date)
        if current is not None:
            logging.info("Using existing bond snapshot archived on %s.", archive_date)
            return current

        try:
            return self._download_snapshot(archive_date)
        except Exception:
            fallback = self._latest_valid_snapshot(archive_date)
            if fallback is None:
                raise
            age = (archive_date - fallback.archive_date).days
            if age > self.max_fallback_age_days:
                raise RuntimeError(
                    "Bond download failed and the latest valid snapshot is "
                    f"{age} days old, beyond the {self.max_fallback_age_days}-day fallback limit."
                )
            logging.warning(
                "Bond download failed; using fallback snapshot archived on %s (%s days old).",
                fallback.archive_date,
                age,
            )
            return BondSnapshot(
                archive_date=fallback.archive_date,
                fd_path=fallback.fd_path,
                bi_path=fallback.bi_path,
                downloaded=False,
                fallback=True,
            )
