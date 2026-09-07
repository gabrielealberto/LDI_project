"""Single Visual Studio entry point for the monthly LDI workflow."""

import pandas as pd

from core.ldi_engine import (
    export_ldi_excel,
    load_bond_inputs,
    optimize_cashflow_matching,
    print_purchase_plan,
)
from core.future_liabilities import baseline_cashflows
from core.inflation_stress_testing import (
    run_inflation_stress_test,
    save_inflation_stress_results,
)
from core.pipeline import refresh_ldi_inputs
from core.plots import plot_portfolio


def print_inflation_stress_results(stress_report):
    """Print the decision-useful frozen-portfolio stress metrics to the Run console."""
    summary = stress_report["summary"].copy()
    labels = {
        "baseline": "Baseline",
        "inflation_upside_200bp": "Inflazione +200 bp",
        "inflation_downside_200bp": "Inflazione -200 bp",
        "italy_spread_widening_100bp": "Spread Italia/EA +100 bp",
        "italy_stagflation": "Stagflazione Italia",
        "deflation_stress": "Stress deflazione",
    }
    summary["label"] = summary["scenario_id"].map(labels).fillna(summary["scenario_id"])
    summary = summary.sort_values("external_funding_eur", ascending=False)

    print("\n" + "=" * 106)
    print("ANALISI SHOCK INFLAZIONE — PORTAFOGLIO FISSO (nessuna ri-ottimizzazione)")
    print("-" * 106)
    print(
        f"{'Scenario':<30} {'Funding esterno':>18} {'Min. cassa pre-funding':>24} "
        f"{'Mesi deficit':>13}  Esito"
    )
    print("-" * 106)
    for row in summary.itertuples(index=False):
        outcome = "ATTENZIONE" if row.external_funding_eur > 0 else "Coperto"
        print(
            f"{row.label:<30} "
            f"EUR {row.external_funding_eur:>14,.2f} "
            f"EUR {row.minimum_pre_funding_cash_balance_eur:>20,.2f} "
            f"{row.deficit_months:>13}  {outcome}"
        )
    worst = summary.iloc[0]
    print("-" * 106)
    print(
        f"Worst case: {worst.label} | funding esterno EUR {worst.external_funding_eur:,.2f} | "
        f"{worst.deficit_months} mesi in deficit"
    )
    print("=" * 106)


def main():
    """Refresh inputs, solve the mandate, then replay and chart frozen stresses."""
    for stage in refresh_ldi_inputs():
        print(f"Pipeline completed: {stage}")
    dates, cashflows = baseline_cashflows()
    matrix, bonds = load_bond_inputs()
    result = optimize_cashflow_matching(
        pd.DataFrame({"date": dates, "cashflow": cashflows}), matrix, bonds
    )
    print_purchase_plan(result)
    print(f"Excel exported: {export_ldi_excel(result)}")
    stress_report = run_inflation_stress_test(result, bonds)
    summary_path, monthly_path = save_inflation_stress_results(stress_report)
    result["inflation_stress"] = stress_report
    print_inflation_stress_results(stress_report)
    print(f"Stress summary exported: {summary_path}")
    print(f"Stress monthly detail exported: {monthly_path}")
    for path in plot_portfolio(result, stress_report=stress_report):
        print(f"Chart exported: {path}")
    return result


if __name__ == "__main__":
    main()
