import math
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

FD_INPUT = RAW_DIR / "bonds_fd.parquet"
BI_INPUT = RAW_DIR / "bonds_bi.parquet"
FD_OUTPUT = PROCESSED_DIR / "fd_clean.parquet"
BI_OUTPUT = PROCESSED_DIR / "bi_clean.parquet"
LIQUID_PRICE_TYPES = frozenset({"LP"})
# Require a meaningful daily nominal volume, rather than admitting an
# isolated EUR 1,000 print as evidence of reliable execution liquidity.
MIN_DAILY_BOND_VOLUME = 20_000


def liquid_bonds(
    df,
    min_daily_volume=MIN_DAILY_BOND_VOLUME,
    allowed_price_types=LIQUID_PRICE_TYPES,
):
    """Select bonds with a traded price and enough daily nominal volume.

    The source uses ``LP`` for a last traded price and ``RP`` when it falls
    back to a reference price.  ``volume`` is parsed defensively because the
    upstream CSV may contain strings or missing values.
    """
    missing = {"pricetype", "volume"} - set(df.columns)
    if missing:
        raise ValueError(f"Missing liquidity columns: {sorted(missing)}")
    try:
        threshold = float(min_daily_volume)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "min_daily_volume must be a finite non-negative number"
        ) from error
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("min_daily_volume must be a finite non-negative number")

    price_types = df["pricetype"].fillna("").astype(str).str.strip().str.upper()
    allowed = {str(value).strip().upper() for value in allowed_price_types}
    volume = pd.to_numeric(df["volume"], errors="coerce")
    return df[price_types.isin(allowed) & volume.ge(threshold)].copy()


def clean_fd(df, min_daily_volume=MIN_DAILY_BOND_VOLUME):
    cleaned = df.copy()
    report = {"initial": len(cleaned)}

    cleaned = cleaned[cleaned["currencycode"].eq("EUR")]
    report["eur"] = len(cleaned)

    issuer = cleaned["issuercode"].fillna("").astype(str)
    cleaned = cleaned[issuer.str.contains("GOV|SOV", case=False, regex=True)]
    report["government"] = len(cleaned)

    cleaned = cleaned[~cleaned["issuercode"].eq("SOV_BEI")]
    report["no_bei"] = len(cleaned)

    low_sp = cleaned["ratingsp"].fillna("").astype(str).eq("BBB-")
    low_moodys = cleaned["ratingmoodys"].fillna("").astype(str).eq("Baa3")
    cleaned = cleaned[~(low_sp | low_moodys)]
    report["rating"] = len(cleaned)

    has_sp = cleaned["ratingsp"].notna() & cleaned["ratingsp"].astype(
        str
    ).str.strip().ne("")
    has_moodys = cleaned["ratingmoodys"].notna() & cleaned["ratingmoodys"].astype(
        str
    ).str.strip().ne("")
    cleaned = cleaned[has_sp | has_moodys]
    report["rated"] = len(cleaned)

    minimum_lot = pd.to_numeric(cleaned["minimumlot"], errors="coerce")
    cleaned = cleaned[minimum_lot.le(1_000)]
    report["minimumlot"] = len(cleaned)

    cleaned = liquid_bonds(cleaned, min_daily_volume=min_daily_volume)
    report["liquid"] = len(cleaned)

    ttm_days = (
        pd.to_datetime(cleaned["redemptiondate"], dayfirst=True)
        - pd.to_datetime(cleaned["referencedate"], dayfirst=True)
    ).dt.days
    cleaned = cleaned[ttm_days >= 365]
    report["maturity_1y"] = len(cleaned)

    cleaned = cleaned.drop(columns=["ratingfitch"])

    return cleaned.reset_index(drop=True), report


def clean_bi(df, fd_clean):
    isin = fd_clean["isincode"].dropna().unique()
    cleaned = df[df["isincode"].isin(isin)].copy()
    cleaned = cleaned.drop(columns=["issueprice", "redemptionprice"])
    return cleaned.reset_index(drop=True)


def run(fd_input=FD_INPUT, bi_input=BI_INPUT):
    """Clean an explicit immutable raw snapshot into the current universe."""
    fd = pd.read_parquet(fd_input)
    bi = pd.read_parquet(bi_input)

    fd_clean, report = clean_fd(fd)
    bi_clean = clean_bi(bi, fd_clean)
    FD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd_clean.to_parquet(
        FD_OUTPUT,
        engine="pyarrow",
        compression="snappy",
        index=False,
    )
    bi_clean.to_parquet(
        BI_OUTPUT,
        engine="pyarrow",
        compression="snappy",
        index=False,
    )

    return {
        "fd": report,
        "bi_rows": len(bi),
        "bi_clean_rows": len(bi_clean),
        "fd_output": FD_OUTPUT,
        "bi_output": BI_OUTPUT,
    }
