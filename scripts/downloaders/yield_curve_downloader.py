from io import BytesIO
from pathlib import Path

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"


class ECBDownloader:
    URL = (
        "https://data-api.ecb.europa.eu/service/data/YC/"
        "B.U2.EUR.4F.G_N_C.SV_C_YM.BETA0+BETA1+BETA2+BETA3+TAU1+TAU2"
    )
    OUTPUT = RAW_DIR / "ecb_svensson.parquet"
    COLUMNS = ("DATA_TYPE_FM", "TIME_PERIOD", "OBS_VALUE")
    PARAMETER_ORDER = ("BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2")

    def __init__(self, output=OUTPUT, timeout=30):
        self.output = Path(output)
        self.timeout = timeout

    def fetch(self):
        response = requests.get(
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

    def save(self, df):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(
            self.output,
            engine="pyarrow",
            compression="snappy",
            index=False,
        )

    def run(self):
        df = self.transform(self.fetch())
        self.save(df)
        return df
