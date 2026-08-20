"""Canonical command-line workflow for the monthly LDI engine."""

import pandas as pd

from ldi_engine import (
    export_ldi_excel,
    load_bond_inputs,
    optimize_cashflow_matching,
    print_purchase_plan,
)
from future_liabilities import portfolio as liability_portfolio
from pipeline import refresh_ldi_inputs


def main():
    """Refresh all inputs, solve the mandate, and export its audit trail."""
    for stage in refresh_ldi_inputs():
        print(f"Pipeline completed: {stage}")
    dates, cashflows = liability_portfolio.merge_liabilities()
    matrix, bonds = load_bond_inputs()
    result = optimize_cashflow_matching(
        pd.DataFrame({"date": dates, "cashflow": cashflows}), matrix, bonds
    )
    print_purchase_plan(result)
    print(f"Excel exported: {export_ldi_excel(result)}")
    return result


if __name__ == "__main__":
    main()
