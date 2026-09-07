from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"


class BondDownloader:
    DOCUMENTS_URL = "https://www.simpletoolsforinvestors.eu/documentivari.php"
    EXPORTS = {
        "bi": {
            "label": "Obbligazioni quotate su Borsa Italiana",
            "fallback_url": "https://www.simpletoolsforinvestors.eu/data/export/CC0BE422AD1AD0C98EE1CD2295F7518E.csv",
            "output": RAW_DIR / "bonds_bi.parquet",
        },
        "fd": {
            "label": "Rendimenti e durate calcolati End of Day",
            "fallback_url": "https://www.simpletoolsforinvestors.eu/data/export/9968FD5CA84626F23636B2751C3BD702.csv",
            "output": RAW_DIR / "bonds_fd.parquet",
        },
    }

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()
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

        for row in soup.select("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            description = cells[0].get_text(" ", strip=True)
            link = cells[1].find("a", href=True)
            if not link:
                continue

            for key, export in self.EXPORTS.items():
                if export["label"] in description:
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

    def run(self):
        discovered_urls = self.discover_urls()
        saved = {}

        for key, export in self.EXPORTS.items():
            url = discovered_urls.get(key, export["fallback_url"])
            df = self.read_csv(self.download_csv(url))
            export["output"].parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(
                export["output"],
                engine="pyarrow",
                compression="snappy",
                index=False,
            )
            saved[key] = {
                "path": export["output"],
                "rows": len(df),
                "columns": len(df.columns),
                "url": url,
            }

        return saved
