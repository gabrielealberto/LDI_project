"""Canonical command-line workflow for the monthly LDI engine."""

import pandas as pd

from core.ldi_engine import (
    export_ldi_excel,
    load_bond_inputs,
    optimize_cashflow_matching,
    print_purchase_plan,
)
from core.future_liabilities import scenario_cashflows
from core.pipeline import refresh_ldi_inputs
from core.scenario_simulation import run_scenario_analysis


def main():
    """Refresh all inputs, solve the mandate, and export its audit trail."""
    for stage in refresh_ldi_inputs():
        print(f"Pipeline completed: {stage}")
    dates, cashflows = scenario_cashflows()
    matrix, bonds = load_bond_inputs()
    result = optimize_cashflow_matching(
        pd.DataFrame({"date": dates, "cashflow": cashflows}), matrix, bonds
    )
    scenario_analysis = run_scenario_analysis(result, matrix, bonds)
    print_purchase_plan(result)
    for name, data in scenario_analysis["scenarios"].items():
        print(
            f"Scenario {name}: probability={data['probability']:.1%} | "
            f"external funding=EUR {data['external_funding_eur']:,.2f}"
        )
    print(f"Excel exported: {export_ldi_excel(result, scenario_analysis=scenario_analysis)}")
    return result


if __name__ == "__main__":
    main()
