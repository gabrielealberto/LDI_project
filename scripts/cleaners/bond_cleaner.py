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

RATING_ORDER = (
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC",
    "CC",
    "C",
    "D",
)
RATING_SCORE = {rating: score for score, rating in enumerate(RATING_ORDER)}
MOODYS_TO_CANONICAL = {
    "AAA": "AAA",
    "AA1": "AA+",
    "AA2": "AA",
    "AA3": "AA-",
    "A1": "A+",
    "A2": "A",
    "A3": "A-",
    "BAA1": "BBB+",
    "BAA2": "BBB",
    "BAA3": "BBB-",
    "BA1": "BB+",
    "BA2": "BB",
    "BA3": "BB-",
    "B1": "B+",
    "B2": "B",
    "B3": "B-",
    "CAA1": "CCC",
    "CAA2": "CC",
    "CAA3": "CC",
    "CA": "C",
    "C": "D",
}
MINIMUM_CLEANING_RATING = "BBB-"


def canonical_rating(value, agency="sp"):
    normalized = str(value).strip().upper() if pd.notna(value) else ""
    if not normalized:
        return None
    if agency == "moodys":
        return MOODYS_TO_CANONICAL.get(normalized)
    return normalized if normalized in RATING_SCORE else None


def effective_rating(row):
    """Return the worst recognized rating available for one bond."""
    ratings = [
        canonical_rating(row.get("ratingsp"), "sp"),
        canonical_rating(row.get("ratingmoodys"), "moodys"),
    ]
    ratings = [rating for rating in ratings if rating is not None]
    return (
        RATING_ORDER[max(RATING_SCORE[rating] for rating in ratings)]
        if ratings
        else None
    )


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

    floor_score = RATING_SCORE[MINIMUM_CLEANING_RATING]
    sp_score = cleaned["ratingsp"].map(
        lambda value: RATING_SCORE.get(canonical_rating(value, "sp"), -1)
    )
    moodys_score = cleaned["ratingmoodys"].map(
        lambda value: RATING_SCORE.get(canonical_rating(value, "moodys"), -1)
    )
    # BBB- is the minimum admitted rating; BB+ and lower are excluded.
    cleaned = cleaned[(sp_score < 0) | (sp_score <= floor_score)]
    moodys_score = moodys_score.loc[cleaned.index]
    sp_score = sp_score.loc[cleaned.index]
    cleaned = cleaned[(moodys_score < 0) | (moodys_score <= floor_score)]
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


def apply_universe_filters(fd, bi, allowed_ratings=None, allowed_issuers=None):
    """Apply per-run rating and issuer filters without changing canonical clean data."""
    filtered = fd.copy()
    if allowed_ratings is not None:
        allowed = {
            canonical_rating(value, "sp") or str(value).strip().upper()
            for value in allowed_ratings
        }
        effective = filtered.apply(effective_rating, axis=1)
        filtered = filtered.loc[effective.isin(allowed)].copy()
    if allowed_issuers is not None:
        allowed = {str(value).strip() for value in allowed_issuers}
        filtered = filtered.loc[filtered["issuercode"].astype(str).isin(allowed)].copy()

    valid_isins = filtered["isincode"].dropna().unique()
    filtered_bi = bi.loc[bi["isincode"].isin(valid_isins)].copy()
    return filtered.reset_index(drop=True), filtered_bi.reset_index(drop=True)


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
