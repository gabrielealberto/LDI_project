"""Build and validate sourced cash-flow schedules for step-up/down government bonds.

The generated JSON is an audit input only.  It is not consumed by the production
cash-flow builder until an explicit integration change is made.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from core.bond_cash_flow_creator import gross_ytm, load_clean_bonds, merge_clean_bonds
from core.utils import NOMINAL


OUTPUT_PATH = Path("data/config/step_up_down_cashflows.json")
YTM_TOLERANCE = 0.02  # percentage points

# Each schedule is transcribed from the linked official exchange, MEF, or
# Gazzetta Ufficiale source.  Period counts are coupon counts, not calendar years.
STEP_UP_DOWN_TERMS = {
    "IT0005565400": {
        "issue_date": "2023-10-10",
        "maturity_date": "2028-10-10",
        "frequency": 4,
        "annual_rates": [0.041, 0.045],
        "period_counts": [12, 8],
        "source_url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005565400-MOTX.html",
    },
    "IT0005583486": {
        "issue_date": "2024-03-05",
        "maturity_date": "2030-03-05",
        "frequency": 4,
        "annual_rates": [0.0325, 0.04],
        "period_counts": [12, 12],
        "source_url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005583486-MOTX.html",
    },
    "IT0005594483": {
        "issue_date": "2024-05-14",
        "maturity_date": "2030-05-14",
        "frequency": 4,
        "annual_rates": [0.0335, 0.039],
        "period_counts": [12, 12],
        "source_url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005594483-MOTX.html",
    },
    "IT0005634800": {
        "issue_date": "2025-02-25",
        "maturity_date": "2033-02-25",
        "frequency": 4,
        "annual_rates": [0.0285, 0.037],
        "period_counts": [16, 16],
        "source_url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005634800-MOTX.html",
    },
    "IT0005672024": {
        "issue_date": "2025-10-28",
        "maturity_date": "2032-10-28",
        "frequency": 4,
        "annual_rates": [0.026, 0.031, 0.04],
        "period_counts": [12, 8, 8],
        "source_url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005672024-MOTX.html",
    },
    "IT0005696338": {
        "issue_date": "2026-03-10",
        "maturity_date": "2032-03-10",
        "frequency": 4,
        "annual_rates": [0.026, 0.032, 0.038],
        "period_counts": [8, 8, 8],
        "source_url": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/IT0005696338-MOTX.html",
    },
    "IT0005415291": {
        "issue_date": "2020-07-14",
        "maturity_date": "2030-07-14",
        "frequency": 2,
        "annual_rates": [0.0115, 0.013, 0.0145],
        "period_counts": [8, 6, 6],
        "source_url": "https://www.borsaitaliana.it/obbligazioni/btp-italia/btpfutura1aemissione.htm",
    },
    "IT0005425761": {
        "issue_date": "2020-11-17",
        "maturity_date": "2028-11-17",
        "frequency": 2,
        "annual_rates": [0.0035, 0.006, 0.01],
        "period_counts": [6, 6, 4],
        "source_url": "https://www.gazzettaufficiale.it/atto/serie_generale/caricaDettaglioAtto/originario?atto.codiceRedazionale=20A06372&atto.dataPubblicazioneGazzetta=2020-11-20",
    },
    "IT0005442097": {
        "issue_date": "2021-04-27",
        "maturity_date": "2037-04-27",
        "frequency": 2,
        "annual_rates": [0.0075, 0.012, 0.0165, 0.02],
        "period_counts": [8, 8, 8, 8],
        "source_url": "https://www.borsaitaliana.it/obbligazioni/btp-italia/btpfuturaiiiinizionegoziazione.pdf",
    },
    "IT0005466351": {
        "issue_date": "2021-11-16",
        "maturity_date": "2033-11-16",
        "frequency": 2,
        "annual_rates": [0.0075, 0.0135, 0.017],
        "period_counts": [8, 8, 8],
        "source_url": "https://www.borsaitaliana.it/obbligazioni/btp-italia/btpfutura4aemissione.htm",
    },
}


def schedule_for_bond(isincode, terms, nominal=NOMINAL):
    """Return vertical contractual coupon and principal flows for one EUR 1,000 lot."""
    issue = pd.Timestamp(terms["issue_date"])
    maturity = pd.Timestamp(terms["maturity_date"])
    months = 12 // terms["frequency"]
    dates = pd.date_range(
        issue + pd.DateOffset(months=months), maturity, freq=pd.DateOffset(months=months)
    )
    rates = np.repeat(terms["annual_rates"], terms["period_counts"])
    if len(dates) != len(rates) or dates[-1] != maturity:
        raise ValueError(f"Invalid coupon schedule for {isincode}")

    return [
        {
            "isincode": isincode,
            "date": date.strftime("%Y-%m-%d"),
            "coupon_eur": round(float(nominal * rate / terms["frequency"]), 8),
            "principal_eur": float(nominal if date == maturity else 0),
            "annual_coupon_rate": rate,
            "frequency_per_year": terms["frequency"],
            "source_url": terms["source_url"],
        }
        for date, rate in zip(dates, rates)
    ]


def calculated_ytm(schedule, market_row, nominal=NOMINAL):
    """Recalculate gross YTM using the same timing convention as the current pipeline."""
    flows = pd.DataFrame(schedule)
    flows["date"] = pd.to_datetime(flows["date"])
    valuation_date = pd.to_datetime(market_row["referencedate"], dayfirst=True)
    prior = flows.loc[flows["date"] <= valuation_date].iloc[-1]
    rate = float(prior["annual_coupon_rate"])
    accrued = (valuation_date - prior["date"]).days / 365 * rate * nominal
    purchase = pd.DataFrame(
        {
            "isincode": [schedule[0]["isincode"]],
            "date": [valuation_date],
            "l1": [-nominal * float(market_row["price"]) / 100],
            "l2": [-accrued],
            "l3": [0.0],
        }
    )
    contractual = flows.assign(l1=flows["principal_eur"], l2=0.0, l3=flows["coupon_eur"])[
        ["isincode", "date", "l1", "l2", "l3"]
    ]
    contractual = contractual.loc[contractual["date"] > valuation_date]
    return gross_ytm(pd.concat([purchase, contractual], ignore_index=True)) * 100


def dataframe_rows(schedule):
    """Convert audit-rich schedule rows to the production cash-flow schema."""
    return [
        {
            "isincode": row["isincode"],
            "date": row["date"],
            "l1": row["principal_eur"],
            "l2": 0.0,
            "l3": row["coupon_eur"],
        }
        for row in schedule
    ]


def build_and_validate():
    """Build schedules and compare their YTM with current pipeline market metrics."""
    fd_clean, bi_clean = load_clean_bonds()
    market = merge_clean_bonds(fd_clean, bi_clean).set_index("isincode")
    schedules = []
    validation = []
    for isincode, terms in STEP_UP_DOWN_TERMS.items():
        schedule = schedule_for_bond(isincode, terms)
        row = market.loc[isincode]
        ytm_calculated = calculated_ytm(schedule, row)
        ytm_source = float(row["grossytm"])
        difference = ytm_calculated - ytm_source
        validation.append(
            {
                "isincode": isincode,
                "ytm_source_percentage": ytm_source,
                "ytm_calculated_percentage": ytm_calculated,
                "difference_percentage_points": difference,
                "matches": bool(np.isclose(ytm_calculated, ytm_source, atol=YTM_TOLERANCE)),
                "cashflow_count": len(schedule),
            }
        )
        schedules.extend(dataframe_rows(schedule))
    return schedules, validation


def main():
    schedules, validation = build_and_validate()
    all_match = all(row["matches"] for row in validation)
    if not all_match:
        print(json.dumps(validation, indent=2))
        print("No contractual cash-flow JSON was written because validation failed.")
        return 1

    OUTPUT_PATH.write_text(json.dumps(schedules, indent=2), encoding="utf-8")
    print(f"Validated bonds: {len(validation)}")
    print(f"Contractual cash-flow rows: {len(schedules)}")
    print(f"Cash-flow JSON: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
