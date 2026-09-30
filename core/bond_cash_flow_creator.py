from pathlib import Path

import numpy as np
import pandas as pd

from .inflation_linked_bonds import load_inflation_linked_bond_types
from .inflation_linked_cashflows import build_inflation_linked_cashflows
from .run_config import UniverseFilters
from scripts.cleaners.bond_cleaner import apply_universe_filters
from .utils import (
    BOND_CASHFLOWS_PATH,
    BOND_CASHFLOW_MATRIX_PATH,
    BI_CLEAN_PATH,
    CONFIG_DIR,
    FD_CLEAN_PATH,
    NOMINAL,
)

STEP_UP_DOWN_CASHFLOWS_PATH = CONFIG_DIR / "step_up_down_cashflows.json"
STANDARD_CASHFLOWS_PATH = CONFIG_DIR / "standard_cashflows.json"


def effective_frequency(couponperiodicity, coupon_rate):
    if coupon_rate == 0:
        return 0
    return couponperiodicity if couponperiodicity > 0 else 1


def load_clean_bonds():
    fd = pd.read_parquet(FD_CLEAN_PATH)
    bi = pd.read_parquet(BI_CLEAN_PATH)
    return fd, bi


def merge_clean_bonds(fd, bi):
    bi_columns = ["isincode", "description", "firstdate", "redemptiondate", "issuedate"]
    bi_to_merge = bi[bi_columns].rename(
        columns={
            "description": "bi_description",
            "firstdate": "bi_firstdate",
            "redemptiondate": "bi_redemptiondate",
            "issuedate": "bi_issuedate",
        }
    )
    return fd.merge(bi_to_merge, on="isincode", how="left", validate="one_to_one")


def calendar(couponmonths, freq, maturity, valuation_date):
    maturity = pd.to_datetime(maturity, dayfirst=True)
    valuation = pd.to_datetime(valuation_date, dayfirst=True)

    if freq == 0 or pd.isna(freq) or pd.isna(couponmonths):
        dates = np.array([maturity], dtype="datetime64[ns]")
    else:
        months = [int(month) for month in str(couponmonths).split(",")]
        dates = [
            pd.Timestamp(year=year, month=month, day=maturity.day)
            for year in range(valuation.year - 1, maturity.year + 1)
            for month in months
        ]
        dates = np.array(
            sorted(date for date in dates if date <= maturity), dtype="datetime64[ns]"
        )

    prev = dates[dates <= np.datetime64(valuation)][-1:]
    future = dates[dates > np.datetime64(valuation)]

    return pd.DatetimeIndex(np.concatenate([prev, future]))


def create_cashflows(bond, nominal=NOMINAL):
    valuation = pd.to_datetime(bond["referencedate"], dayfirst=True)
    maturity = pd.to_datetime(bond["redemptiondate"], dayfirst=True)
    frequency = effective_frequency(
        bond["couponperiodicity"], bond["currentcouponrate"]
    )
    dates = calendar(
        bond["couponmonths"],
        frequency,
        bond["redemptiondate"],
        bond["referencedate"],
    )

    date_values = dates.to_numpy()
    future = date_values > np.datetime64(valuation)
    previous = date_values[date_values <= np.datetime64(valuation)]
    previous_date = pd.Timestamp(previous[-1]) if len(previous) else pd.NaT

    l1 = np.where(date_values == np.datetime64(maturity), nominal, 0.0)
    l2 = np.zeros(len(date_values))
    l3 = np.zeros(len(date_values))

    if frequency > 0:
        coupon = nominal * bond["currentcouponrate"] / frequency
        accrued = (
            0
            if pd.isna(previous_date)
            else (valuation - previous_date).days
            / 365
            * (bond["currentcouponrate"] * nominal)
        )
        l3 = np.where(future, coupon, 0.0)
    else:
        accrued = 0

    cf = pd.DataFrame(
        {
            "isincode": bond["isincode"],
            "date": dates,
            "l1": l1,
            "l2": l2,
            "l3": l3,
        }
    )

    purchase = pd.DataFrame(
        {
            "isincode": [bond["isincode"]],
            "date": [valuation],
            "l1": [-nominal * bond["price"] / 100],
            "l2": [-accrued],
            "l3": [0.0],
        }
    )

    return (
        pd.concat([purchase, cf], ignore_index=True)
        .loc[lambda df: df[["l1", "l2", "l3"]].ne(0).any(axis=1)]
        .sort_values("date")
        .reset_index(drop=True)
    )


def create_all_cashflows(bonds, nominal=NOMINAL):
    return pd.concat(
        [
            create_cashflows(bond._asdict(), nominal=nominal)
            for bond in bonds.itertuples(index=False)
        ],
        ignore_index=True,
    )


def gross_ytm(cashflows):
    cf = cashflows.copy()
    cf["total"] = cf[["l1", "l2", "l3"]].sum(axis=1)
    purchase_date = cf.loc[cf["total"].lt(0), "date"].iloc[0]
    t = ((cf["date"] - purchase_date) / pd.Timedelta(days=365)).to_numpy()
    amounts = cf["total"].to_numpy()

    def npv(rate):
        return np.sum(amounts / (1 + rate) ** t)

    low, high = -0.99, 1.0
    npv_low = npv(low)
    for _ in range(100):
        mid = (low + high) / 2
        npv_mid = npv(mid)
        if npv_low * npv_mid <= 0:
            high = mid
        else:
            low = mid
            npv_low = npv_mid
        if high - low <= 1e-14:
            break

    return (low + high) / 2


def _compare_gross_ytm_with_cashflows(bonds, nominal=NOMINAL, tolerance=0.02):
    rows = []
    cashflow_parts = []
    for row in bonds.itertuples(index=False):
        bond = row._asdict()
        cashflows = create_cashflows(bond, nominal=nominal)
        cashflow_parts.append(cashflows)
        ytm = gross_ytm(cashflows)
        source = round(bond["grossytm"], 2)
        calculated = round(ytm * 100, 2)
        diff = calculated - source
        rows.append(
            {
                "isincode": bond["isincode"],
                "grossytm_source": source,
                "grossytm_calc": calculated,
                "grossytm_diff": diff,
                "is_equal": np.isclose(calculated, source, atol=tolerance),
            }
        )

    comparison = pd.DataFrame(rows)
    if cashflow_parts:
        cashflows = pd.concat(cashflow_parts, ignore_index=True)
    else:
        cashflows = pd.DataFrame(columns=["isincode", "date", "l1", "l2", "l3"])
    return comparison, cashflows


def compare_gross_ytm(bonds, nominal=NOMINAL, tolerance=0.02):
    comparison, _ = _compare_gross_ytm_with_cashflows(
        bonds, nominal=nominal, tolerance=tolerance
    )
    return comparison


def validated_bonds(bonds, nominal=NOMINAL):
    comparison = compare_gross_ytm(bonds, nominal=nominal)
    valid_isin = comparison.loc[comparison["is_equal"], "isincode"]
    return bonds[bonds["isincode"].isin(valid_isin)].reset_index(drop=True), comparison


def _validated_bonds_with_cashflows(bonds, nominal=NOMINAL):
    """Validate bonds once and return the generic flows for reuse downstream."""
    comparison, cashflows = _compare_gross_ytm_with_cashflows(bonds, nominal=nominal)
    valid_isin = comparison.loc[comparison["is_equal"], "isincode"]
    validated = bonds[bonds["isincode"].isin(valid_isin)].reset_index(drop=True)
    return validated, comparison, cashflows


def load_cashflow_overrides(
    paths=(STEP_UP_DOWN_CASHFLOWS_PATH, STANDARD_CASHFLOWS_PATH),
):
    """Load hand-validated contractual cash flows in the native output schema."""
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    overrides = pd.concat(
        [pd.read_json(path, convert_dates=["date"]) for path in paths],
        ignore_index=True,
    )
    required = {"isincode", "date", "l1", "l2", "l3"}
    missing = required - set(overrides.columns)
    if missing:
        raise ValueError(f"Cash-flow override is missing columns: {sorted(missing)}")
    overrides = overrides[["isincode", "date", "l1", "l2", "l3"]].copy()
    overrides["isincode"] = overrides["isincode"].astype(str)
    overrides["date"] = pd.to_datetime(overrides["date"])
    if overrides[["l1", "l2", "l3"]].isna().any().any():
        raise ValueError("Cash-flow override contains missing cash-flow values.")
    if overrides.duplicated(["isincode", "date"]).any():
        raise ValueError("Cash-flow override contains duplicate ISIN/date rows.")
    return overrides.sort_values(["isincode", "date"]).reset_index(drop=True)


def apply_cashflow_overrides(cashflows, overrides, bonds, nominal=NOMINAL):
    """Replace generated future flows for each override ISIN, without touching other bonds."""
    replacement_isins = set(overrides["isincode"])
    reference_dates = (
        bonds[["isincode", "referencedate"]].drop_duplicates("isincode").copy()
    )
    reference_dates["referencedate"] = pd.to_datetime(
        reference_dates["referencedate"], dayfirst=True
    )
    cashflows_with_reference = cashflows.merge(
        reference_dates, on="isincode", how="left", validate="many_to_one"
    )
    retained = cashflows_with_reference.loc[
        ~cashflows_with_reference["isincode"].isin(replacement_isins)
        | (
            cashflows_with_reference["date"]
            <= cashflows_with_reference["referencedate"]
        )
    ].drop(columns="referencedate")
    effective_overrides = overrides.merge(
        reference_dates, on="isincode", how="left", validate="many_to_one"
    )
    effective_overrides = effective_overrides.loc[
        effective_overrides["date"] > effective_overrides["referencedate"]
    ].drop(columns="referencedate")
    effective_overrides[["l1", "l2", "l3"]] *= float(nominal) / NOMINAL
    return (
        pd.concat([retained, effective_overrides], ignore_index=True)
        .sort_values(["isincode", "date"])
        .reset_index(drop=True)
    )


def monthly_cashflow_matrix(cashflows):
    cf = cashflows.copy()
    cf["month"] = cf["date"].dt.to_period("M").astype(str)
    cf["total"] = cf[["l1", "l2", "l3"]].sum(axis=1)
    return cf.pivot_table(
        index="isincode",
        columns="month",
        values="total",
        aggfunc="sum",
        fill_value=0.0,
    )


def build_cashflow_outputs(nominal=NOMINAL, universe_filters=None):
    """Generate and persist the validated detailed and monthly bond cash flows."""
    fd_clean, bi_clean = load_clean_bonds()
    overrides = load_cashflow_overrides()
    override_isins = set(overrides["isincode"])
    cleaned_isins = set(fd_clean["isincode"])
    missing_override_bonds = override_isins - cleaned_isins
    if missing_override_bonds:
        raise ValueError(
            f"Override ISINs are absent from the clean bond universe: {sorted(missing_override_bonds)}"
        )

    filters = (
        universe_filters
        if isinstance(universe_filters, UniverseFilters)
        else UniverseFilters.from_mapping(universe_filters)
    )
    fd_filtered, bi_filtered = apply_universe_filters(
        fd_clean,
        bi_clean,
        allowed_ratings=filters.allowed_ratings,
        allowed_issuers=filters.allowed_issuers,
    )
    configured_inflation_linked_isins = set(load_inflation_linked_bond_types())
    if filters.include_inflation_linked:
        # Inflation-linked instruments remain controlled by their dedicated
        # include/exclude switch rather than disappearing because of a market
        # rating or issuer selection intended for nominal bonds.
        ilb_fd = fd_clean.loc[
            fd_clean["isincode"].isin(configured_inflation_linked_isins)
        ]
        ilb_bi = bi_clean.loc[
            bi_clean["isincode"].isin(configured_inflation_linked_isins)
        ]
        fd_filtered = (
            pd.concat([fd_filtered, ilb_fd], ignore_index=True)
            .drop_duplicates("isincode")
            .reset_index(drop=True)
        )
        bi_filtered = (
            pd.concat([bi_filtered, ilb_bi], ignore_index=True)
            .drop_duplicates("isincode")
            .reset_index(drop=True)
        )
    all_bonds = merge_clean_bonds(fd_filtered, bi_filtered)
    if all_bonds.empty:
        raise ValueError("Universe filters leave no eligible bonds.")

    bonds, comparison, validated_cashflows = _validated_bonds_with_cashflows(
        all_bonds, nominal=nominal
    )
    # These structured bonds are validated against their explicit schedules,
    # rather than the generic fixed-coupon cash-flow generator.
    override_bonds = all_bonds.loc[all_bonds["isincode"].isin(override_isins)]
    bonds = (
        pd.concat([bonds.loc[~bonds["isincode"].isin(override_isins)], override_bonds])
        .drop_duplicates("isincode")
        .reset_index(drop=True)
    )
    inflation_linked_isins = (
        configured_inflation_linked_isins if filters.include_inflation_linked else set()
    )
    inflation_linked_bonds = all_bonds.loc[
        all_bonds["isincode"].isin(inflation_linked_isins)
    ].reset_index(drop=True)
    missing_inflation_linked = inflation_linked_isins - set(
        inflation_linked_bonds["isincode"]
    )
    if missing_inflation_linked:
        raise ValueError(
            "Inflation-linked configuration ISINs are absent from the clean universe: "
            f"{sorted(missing_inflation_linked)}"
        )
    nominal_bonds = bonds.loc[
        ~bonds["isincode"].isin(inflation_linked_isins)
    ].reset_index(drop=True)
    nominal_isins = set(nominal_bonds["isincode"])
    cashflows = validated_cashflows.loc[
        validated_cashflows["isincode"].isin(nominal_isins)
    ].copy()
    cashflows = apply_cashflow_overrides(
        cashflows, overrides, nominal_bonds, nominal=nominal
    )
    inflation_cashflows = build_inflation_linked_cashflows(
        inflation_linked_bonds, nominal_per_lot=nominal
    )
    cashflows = pd.concat([cashflows, inflation_cashflows], ignore_index=True)
    bonds = pd.concat([nominal_bonds, inflation_linked_bonds], ignore_index=True)
    matrix = monthly_cashflow_matrix(cashflows)
    cashflows.to_parquet(
        BOND_CASHFLOWS_PATH, engine="pyarrow", compression="snappy", index=False
    )
    matrix.to_parquet(BOND_CASHFLOW_MATRIX_PATH, engine="pyarrow", compression="snappy")
    return {
        "bonds": bonds,
        "comparison": comparison,
        "cashflows": cashflows,
        "matrix": matrix,
        "inflation_linked_bonds": sorted(inflation_linked_isins),
        "universe_filters": filters.to_dict(),
        "universe_counts": {
            "cleaned_before_filters": len(fd_clean),
            "after_filters": len(all_bonds),
            "cashflow_bonds": len(bonds),
        },
    }
