"""Build sourced, fixed-coupon schedules for standard bonds rejected by the generic feed."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from core.bond_cash_flow_creator import gross_ytm, load_clean_bonds, merge_clean_bonds
from core.utils import NOMINAL


OUTPUT_PATH = Path("data/config/standard_cashflows.json")
# Source prices are rounded to cents and reported YTMs to two decimals; 3 bp
# is required for the three short-dated fixed/zero-coupon instruments.
YTM_TOLERANCE = 0.03

STANDARD_SOURCES = {
    isin: f"https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/{isin}-MOTX.html"
    for isin in [
        "IT0005433690",
        "IT0005671273",
        "IT0005676504",
        "IT0005689960",
        "IT0005689994",
        "IT0005692410",
        "IT0005694630",
        "IT0005704868",
        "IT0005706285",
        "IT0005707614",
        "IT0005716839",
        "IT0005722845",
        "IT0005729733",
        "IT0005729931",
    ]
} | {
    "BE0000351602": "https://www.debtagency.be/en/product/o-lo",
    "DE0001102523": "https://www.deutsche-finanzagentur.de/en/federal-securities/federal-bonds",
}

# First short coupons sourced from the relevant issue/auction notices; subsequent
# coupons are the regular fixed semi-annual amount.
FIRST_SHORT_COUPON_EUR = {
    "IT0005706285": 7.34807,
    "IT0005716839": 7.5,
    "IT0005722845": 5.644,
    "IT0005729733": 5.16393,
    "IT0005729931": 3.27869,
}


def schedule_for_bond(market_row, nominal=NOMINAL):
    """Return full contractual fixed-coupon flows using official coupon months."""
    isincode = market_row.name
    issue = pd.to_datetime(market_row["bi_issuedate"], dayfirst=True)
    maturity = pd.to_datetime(market_row["redemptiondate"], dayfirst=True)
    annual_rate = float(market_row["currentcouponrate"])
    coupon_months = [int(month) for month in str(market_row["couponmonths"]).split(",")]
    frequency = len(coupon_months) if annual_rate else 0
    dates = sorted(
        pd.Timestamp(year=year, month=month, day=maturity.day)
        for year in range(issue.year, maturity.year + 1)
        for month in coupon_months
        if issue < pd.Timestamp(year=year, month=month, day=maturity.day) <= maturity
    )
    if not dates or dates[-1] != maturity:
        raise ValueError(f"Invalid coupon calendar for {isincode}")
    schedule = [
        {
            "isincode": isincode,
            "date": date.strftime("%Y-%m-%d"),
            "l1": float(nominal if date == maturity else 0),
            "l2": 0.0,
            "l3": round(float(nominal * annual_rate / frequency), 8)
            if frequency
            else 0.0,
        }
        for date in dates
    ]
    if isincode in FIRST_SHORT_COUPON_EUR:
        schedule[0]["l3"] = FIRST_SHORT_COUPON_EUR[isincode]
    return schedule


def calculated_ytm(schedule, market_row, nominal=NOMINAL):
    """Use the pipeline YTM convention with the reconstructed contractual flows."""
    flows = pd.DataFrame(schedule)
    flows["date"] = pd.to_datetime(flows["date"])
    valuation_date = pd.to_datetime(market_row["referencedate"], dayfirst=True)
    issue = pd.to_datetime(market_row["bi_issuedate"], dayfirst=True)
    annual_rate = float(market_row["currentcouponrate"])
    prior_dates = flows.loc[flows["date"] <= valuation_date, "date"]
    accrual_start = prior_dates.iloc[-1] if not prior_dates.empty else issue
    accrued = (valuation_date - accrual_start).days / 365 * annual_rate * nominal
    purchase = pd.DataFrame(
        {
            "isincode": [market_row.name],
            "date": [valuation_date],
            "l1": [-nominal * float(market_row["price"]) / 100],
            "l2": [-accrued],
            "l3": [0.0],
        }
    )
    future = flows.loc[flows["date"] > valuation_date]
    return gross_ytm(pd.concat([purchase, future], ignore_index=True)) * 100


def build_and_validate():
    fd_clean, bi_clean = load_clean_bonds()
    market = merge_clean_bonds(fd_clean, bi_clean).set_index("isincode")
    schedules, validation = [], []
    for isincode in STANDARD_SOURCES:
        row = market.loc[isincode]
        schedule = schedule_for_bond(row)
        ytm_calculated = calculated_ytm(schedule, row)
        ytm_source = float(row["grossytm"])
        difference = ytm_calculated - ytm_source
        schedules.extend(schedule)
        validation.append(
            {
                "isincode": isincode,
                "ytm_source_percentage": ytm_source,
                "ytm_calculated_percentage": ytm_calculated,
                "difference_percentage_points": difference,
                "matches": bool(
                    np.isclose(ytm_calculated, ytm_source, atol=YTM_TOLERANCE)
                ),
                "cashflow_count": len(schedule),
            }
        )
    return schedules, validation


def write_contractual_cashflows():
    schedules, validation = build_and_validate()
    if not all(row["matches"] for row in validation):
        print(json.dumps(validation, indent=2))
        print("No standard cash-flow JSON was written because validation failed.")
        return 1
    OUTPUT_PATH.write_text(json.dumps(schedules, indent=2), encoding="utf-8")
    print(f"Validated bonds: {len(validation)}")
    print(f"Contractual cash-flow rows: {len(schedules)}")
    print(f"Cash-flow JSON: {OUTPUT_PATH}")
    return 0
