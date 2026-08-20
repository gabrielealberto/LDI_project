import numpy as np
import pandas as pd

from utils import (
    BOND_CASHFLOWS_PATH,
    BOND_CASHFLOW_MATRIX_PATH,
    BI_CLEAN_PATH,
    FD_CLEAN_PATH,
    NOMINAL,
)


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
        dates = np.array(sorted(date for date in dates if date <= maturity), dtype="datetime64[ns]")

    prev = dates[dates <= np.datetime64(valuation)][-1:]
    future = dates[dates > np.datetime64(valuation)]

    return pd.DatetimeIndex(np.concatenate([prev, future]))


def create_cashflows(bond, nominal=NOMINAL):
    valuation = pd.to_datetime(bond["referencedate"], dayfirst=True)
    maturity = pd.to_datetime(bond["redemptiondate"], dayfirst=True)
    frequency = effective_frequency(bond["couponperiodicity"], bond["currentcouponrate"])
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
            else (valuation - previous_date).days / 365 * (bond["currentcouponrate"] * nominal)
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

    return (low + high) / 2


def compare_gross_ytm(bonds, nominal=NOMINAL, tolerance=0.02):
    rows = []
    for row in bonds.itertuples(index=False):
        bond = row._asdict()
        cashflows = create_cashflows(bond, nominal=nominal)
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

    return pd.DataFrame(rows)


def validated_bonds(bonds, nominal=NOMINAL):
    comparison = compare_gross_ytm(bonds, nominal=nominal)
    valid_isin = comparison.loc[comparison["is_equal"], "isincode"]
    return bonds[bonds["isincode"].isin(valid_isin)].reset_index(drop=True), comparison


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


def build_cashflow_outputs():
    """Generate and persist the validated detailed and monthly bond cash flows."""
    fd_clean, bi_clean = load_clean_bonds()
    bonds = merge_clean_bonds(fd_clean, bi_clean)
    bonds, comparison = validated_bonds(bonds)
    cashflows = create_all_cashflows(bonds)
    matrix = monthly_cashflow_matrix(cashflows)
    cashflows.to_parquet(BOND_CASHFLOWS_PATH, engine="pyarrow", compression="snappy", index=False)
    matrix.to_parquet(BOND_CASHFLOW_MATRIX_PATH, engine="pyarrow", compression="snappy")
    return {
        "bonds": bonds,
        "comparison": comparison,
        "cashflows": cashflows,
        "matrix": matrix,
    }


if __name__ == "__main__":
    result = build_cashflow_outputs()

    print(f"Validated bonds: {len(result['bonds'])}")
    print(f"cashflows: {result['cashflows'].shape}")
    print(f"matrix: {result['matrix'].shape}")
    print(f"Saved: {BOND_CASHFLOWS_PATH}")
    print(f"Saved: {BOND_CASHFLOW_MATRIX_PATH}")
