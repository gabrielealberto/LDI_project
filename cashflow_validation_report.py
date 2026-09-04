"""Report bonds discarded because their modelled cash flows fail the YTM check."""

import argparse
from pathlib import Path

import pandas as pd

from bond_cash_flow_creator import (
    compare_gross_ytm,
    gross_ytm,
    load_cashflow_overrides,
    load_clean_bonds,
    merge_clean_bonds,
)
from inflation_linked_bonds import load_inflation_linked_bond_types
from utils import NOMINAL, PROCESSED_DIR


DEFAULT_OUTPUT_PATH = PROCESSED_DIR / "cashflow_validation_report.csv"


def cashflow_structure(isincode, description, inflation_linked_bond_types=None):
    """Classify cash-flow structures that need different validation models."""
    inflation_linked_bond_types = (
        load_inflation_linked_bond_types()
        if inflation_linked_bond_types is None
        else inflation_linked_bond_types
    )
    if str(isincode) in inflation_linked_bond_types:
        return "inflation_linked"
    text = str(description).upper()
    if "BTPI" in text or "INFLATION" in text or "INDICIZZ" in text:
        return "inflation_linked"
    if "STEP UP" in text or "STEP DOWN" in text:
        return "step_up_down"
    return "standard"


def override_gross_ytm(override_flows, market_row):
    """Calculate YTM from an override schedule and the pipeline price metric."""
    flows = override_flows.sort_values("date").copy()
    valuation_date = pd.to_datetime(market_row["referencedate"], dayfirst=True)
    prior_flows = flows.loc[flows["date"] <= valuation_date]
    next_coupon = flows.loc[flows["date"] > valuation_date].iloc[0]
    if prior_flows.empty:
        accrual_start = pd.to_datetime(market_row["bi_issuedate"], dayfirst=True)
        annual_coupon_rate = float(market_row["currentcouponrate"])
    else:
        prior = prior_flows.iloc[-1]
        accrual_start = prior["date"]
        months_per_coupon = (next_coupon["date"].year - prior["date"].year) * 12 + (
            next_coupon["date"].month - prior["date"].month
        )
        annual_coupon_rate = next_coupon["l3"] * (12 / months_per_coupon) / NOMINAL
    accrued = (valuation_date - accrual_start).days / 365 * annual_coupon_rate * NOMINAL
    purchase = pd.DataFrame(
        {
            "isincode": [market_row.name],
            "date": [valuation_date],
            "l1": [-NOMINAL * float(market_row["price"]) / 100],
            "l2": [-accrued],
            "l3": [0.0],
        }
    )
    future = flows.loc[flows["date"] > valuation_date, ["isincode", "date", "l1", "l2", "l3"]]
    return gross_ytm(pd.concat([purchase, future], ignore_index=True)) * 100


def problematic_cashflow_bonds(tolerance=0.02):
    """Return the YTM comparison and every bond rejected by the cash-flow check."""
    fd_clean, bi_clean = load_clean_bonds()
    bonds = merge_clean_bonds(fd_clean, bi_clean)
    comparison = compare_gross_ytm(bonds, tolerance=tolerance)
    inflation_linked_bond_types = load_inflation_linked_bond_types()
    market = bonds.set_index("isincode")
    overrides = load_cashflow_overrides()
    for isincode, flows in overrides.groupby("isincode"):
        if isincode not in market.index:
            continue
        ytm_source = float(market.loc[isincode, "grossytm"])
        ytm_calculated = override_gross_ytm(flows, market.loc[isincode])
        difference = ytm_calculated - ytm_source
        structure = cashflow_structure(
            isincode, market.loc[isincode, "description"], inflation_linked_bond_types
        )
        override_tolerance = 0.03 if structure == "standard" else tolerance
        comparison.loc[comparison["isincode"].eq(isincode), [
            "grossytm_calc",
            "grossytm_diff",
            "is_equal",
        ]] = [ytm_calculated, difference, abs(difference) <= override_tolerance]

    comparison["cashflow_structure"] = [
        cashflow_structure(isincode, description, inflation_linked_bond_types)
        for isincode, description in zip(comparison["isincode"], bonds["description"])
    ]
    # A fixed-coupon model cannot validate an inflation-linked payoff, even
    # when its simplified YTM happens to match the upstream feed metric.
    comparison.loc[comparison["cashflow_structure"].eq("inflation_linked"), "is_equal"] = False

    metadata_columns = [column for column in ["isincode", "description"] if column in bonds]
    rejected = comparison.loc[~comparison["is_equal"]].merge(
        bonds[metadata_columns], on="isincode", how="left", validate="one_to_one"
    )
    rejected["reason"] = rejected["cashflow_structure"].map(
        lambda structure: (
            "inflation_linked_requires_dynamic_model"
            if structure == "inflation_linked"
            else "gross_ytm_mismatch"
        )
    )
    rejected["tolerance_percentage_points"] = tolerance
    rejected["absolute_difference_percentage_points"] = rejected["grossytm_diff"].abs()
    rejected = rejected[
        [
            "isincode",
            "description",
            "reason",
            "cashflow_structure",
            "grossytm_source",
            "grossytm_calc",
            "grossytm_diff",
            "absolute_difference_percentage_points",
            "tolerance_percentage_points",
        ]
    ].sort_values(
        ["cashflow_structure", "absolute_difference_percentage_points"],
        ascending=[True, False],
    )
    return comparison, rejected.reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(
        description="Report bonds rejected because their calculated YTM does not match the source YTM."
    )
    parser.add_argument("--tolerance", type=float, default=0.02, help="YTM tolerance in percentage points")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="CSV report path")
    parser.add_argument(
        "--strict", action="store_true", help="Return exit code 1 when at least one bond is rejected"
    )
    args = parser.parse_args()

    comparison, rejected = problematic_cashflow_bonds(args.tolerance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rejected.to_csv(args.output, index=False)

    print(f"Bonds checked: {len(comparison)}")
    print(f"Cash-flow/YTM matches: {int(comparison['is_equal'].sum())}")
    print(f"Rejected: {len(rejected)}")
    print("Rejected by cash-flow structure:")
    for structure, count in rejected["cashflow_structure"].value_counts().sort_index().items():
        print(f"  {structure}: {count}")
    print(f"Tolerance: {args.tolerance:.2f} percentage points")
    print(f"Report: {args.output}")
    if rejected.empty:
        print("No problematic bonds found.")
    else:
        for structure, group in rejected.groupby("cashflow_structure", sort=True):
            print(f"\nRejected bonds [{structure}]:")
            print(group.to_string(index=False))

    return 1 if args.strict and not rejected.empty else 0


if __name__ == "__main__":
    raise SystemExit(main())
