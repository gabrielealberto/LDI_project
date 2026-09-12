from dataclasses import dataclass
from datetime import date
from io import BytesIO
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from core.ingestion_support import atomic_to_parquet, retry_session


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
CURVE_ARCHIVE_DIR = RAW_DIR / "curves"
MAX_FALLBACK_AGE_DAYS = 5


@dataclass(frozen=True)
class CurveSnapshot:
    """One immutable ECB Svensson curve archived on a calendar day."""

    archive_date: date
    path: Path
    downloaded: bool
    fallback: bool


class ECBDownloader:
    URL = (
        "https://data-api.ecb.europa.eu/service/data/YC/"
        "B.U2.EUR.4F.G_N_C.SV_C_YM.BETA0+BETA1+BETA2+BETA3+TAU1+TAU2"
    )
    COLUMNS = ("DATA_TYPE_FM", "TIME_PERIOD", "OBS_VALUE")
    PARAMETER_ORDER = ("BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2")

    def __init__(
        self,
        timeout=30,
        archive_dir=CURVE_ARCHIVE_DIR,
        max_fallback_age_days=MAX_FALLBACK_AGE_DAYS,
    ):
        self.timeout = timeout
        self.archive_dir = Path(archive_dir)
        self.max_fallback_age_days = max_fallback_age_days
        self.session = retry_session()

    def fetch(self):
        response = self.session.get(
            self.URL,
            params={
                "format": "csvdata",
                "detail": "dataonly",
                "lastNObservations": "1",
            },
            headers={
                "Accept": "text/csv",
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": "mamma-ldi-portfolio/1.0",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()

        df = pd.read_csv(
            BytesIO(response.content),
            usecols=lambda column: column in self.COLUMNS,
        )

        missing = set(self.COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(
                f"Unexpected ECB response: missing columns {sorted(missing)}. "
                f"Received columns: {df.columns.tolist()}"
            )
        if df.empty:
            raise ValueError("The ECB response contains no observations.")

        return df

    def transform(self, df):
        df = df.loc[:, self.COLUMNS].copy()
        try:
            df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"])
            df["VALUE"] = pd.to_numeric(df["OBS_VALUE"])
        except (TypeError, ValueError) as error:
            raise ValueError(
                "The ECB response contains an invalid date or value."
            ) from error
        df["PARAMETER"] = df["DATA_TYPE_FM"]
        df = df.dropna(subset=["TIME_PERIOD", "VALUE", "PARAMETER"])

        order = {name: index for index, name in enumerate(self.PARAMETER_ORDER)}
        df = df[df["PARAMETER"].isin(order)]
        df = df.assign(_parameter_order=df["PARAMETER"].map(order))
        df = df.sort_values(["TIME_PERIOD", "_parameter_order"])

        return df[["TIME_PERIOD", "PARAMETER", "VALUE"]].reset_index(drop=True)

    def _archive_path(self, archive_date: date) -> Path:
        return self.archive_dir / f"yc_{archive_date:%Y%m%d}.parquet"

    def _valid_snapshot(self, archive_date: date) -> CurveSnapshot | None:
        path = self._archive_path(archive_date)
        if not path.is_file():
            return None
        try:
            frame = pd.read_parquet(path, columns=["TIME_PERIOD", "PARAMETER", "VALUE"])
            values = pd.to_numeric(frame["VALUE"], errors="coerce")
            if (
                frame.empty
                or frame["TIME_PERIOD"].isna().any()
                or not np.isfinite(values).all()
                or set(frame["PARAMETER"]) != set(self.PARAMETER_ORDER)
                or frame["PARAMETER"].duplicated().any()
            ):
                return None
        except (OSError, ValueError, KeyError):
            return None
        return CurveSnapshot(archive_date, path, downloaded=False, fallback=False)

    def _latest_valid_snapshot(self, before: date) -> CurveSnapshot | None:
        candidates = []
        for path in self.archive_dir.glob("yc_????????.parquet"):
            try:
                stamp = path.stem.removeprefix("yc_")
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

    def _download_snapshot(self, archive_date: date) -> CurveSnapshot:
        frame = self.transform(self.fetch())
        path = self._archive_path(archive_date)
        atomic_to_parquet(frame, path)
        return CurveSnapshot(archive_date, path, downloaded=True, fallback=False)

    def run(self, archive_date: date | None = None) -> CurveSnapshot:
        """Reuse today's curve, download it once, or use a recent fallback."""
        archive_date = archive_date or date.today()
        current = self._valid_snapshot(archive_date)
        if current is not None:
            logging.info(
                "Using existing ECB curve snapshot archived on %s.", archive_date
            )
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
                    "ECB curve download failed and the latest valid snapshot is "
                    f"{age} days old, beyond the {self.max_fallback_age_days}-day fallback limit."
                )
            logging.warning(
                "ECB curve download failed; using fallback snapshot archived on %s (%s days old).",
                fallback.archive_date,
                age,
            )
            return CurveSnapshot(fallback.archive_date, fallback.path, False, True)
