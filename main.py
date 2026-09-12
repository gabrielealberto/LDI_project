"""Single Visual Studio entry point for the monthly LDI workflow."""

from pathlib import Path

import pandas as pd

from core.ldi_engine import (
    export_ldi_excel,
    load_bond_inputs,
    optimize_cashflow_matching,
    print_purchase_plan,
    save_selection_explanations,
)
from core.utils import BOND_CASHFLOWS_PATH
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
        "transitory_inflation_upside_200bp": "Inflazione transitoria +200 bp",
        "transitory_deflation_250bp": "Deflazione transitoria -250 bp",
        "transitory_italy_ea_spread_100bp": "Spread transitorio Italia/EA +100 bp",
        "persistent_inflation_150bp": "Inflazione persistente +150 bp",
        "persistent_italy_stagflation": "Stagflazione persistente Italia",
        "regime_hicp_3pct": "Regime strategico HICP 3%",
        "regime_hicp_1pct": "Regime strategico HICP 1%",
    }
    summary["label"] = summary["scenario_id"].map(labels).fillna(summary["scenario_id"])
    summary = summary.sort_values("external_funding_eur", ascending=False)

    print("\n" + "=" * 116)
    print("ANALISI SHOCK INFLAZIONE — PORTAFOGLIO FISSO (nessuna ri-ottimizzazione)")
    print("-" * 116)
    print(
        f"{'Famiglia':<14} {'Scenario':<35} {'Funding esterno':>18} "
        f"{'Min. cassa pre-funding':>24} {'Mesi deficit':>13}  Esito"
    )
    print("-" * 116)
    for row in summary.itertuples(index=False):
        outcome = "ATTENZIONE" if row.external_funding_eur > 0 else "Coperto"
        print(
            f"{row.scenario_family:<14} {row.label:<35} "
            f"EUR {row.external_funding_eur:>14,.2f} "
            f"EUR {row.minimum_pre_funding_cash_balance_eur:>20,.2f} "
            f"{row.deficit_months:>13}  {outcome}"
        )
    worst = summary.iloc[0]
    print("-" * 116)
    print(
        f"Worst case: {worst.label} | funding esterno EUR {worst.external_funding_eur:,.2f} | "
        f"{worst.deficit_months} mesi in deficit"
    )
    print("=" * 116)


def main(audit=None):
    """Refresh inputs, solve the mandate, then replay and chart frozen stresses."""
    refreshed = (
        refresh_ldi_inputs(audit=audit) if audit is not None else refresh_ldi_inputs()
    )
    for stage in refreshed:
        print(f"Pipeline completed: {stage}")
    dates, cashflows = baseline_cashflows()
    matrix, bonds = load_bond_inputs()
    detailed_cashflows = pd.read_parquet(BOND_CASHFLOWS_PATH)
    result = optimize_cashflow_matching(
        pd.DataFrame({"date": dates, "cashflow": cashflows}),
        matrix,
        bonds,
        detailed_cashflows=detailed_cashflows,
    )
    if audit is not None:
        audit.record_solver(result)
    explanations_path = save_selection_explanations(result)
    if audit is not None:
        audit.record_output("selection_explanations", explanations_path)
    print_purchase_plan(result)
    excel_path = export_ldi_excel(result)
    if audit is not None:
        audit.record_output("excel_report", excel_path)
    print(f"Excel exported: {excel_path}")
    stress_report = run_inflation_stress_test(result, bonds)
    summary_path, monthly_path = save_inflation_stress_results(stress_report)
    if audit is not None:
        audit.record_output("inflation_stress_summary", summary_path)
        audit.record_output("inflation_stress_monthly", monthly_path)
    result["inflation_stress"] = stress_report
    print_inflation_stress_results(stress_report)
    print(f"Stress summary exported: {summary_path}")
    print(f"Stress monthly detail exported: {monthly_path}")
    for path in plot_portfolio(result, stress_report=stress_report):
        if audit is not None:
            audit.record_output(f"plot_{Path(path).stem}", path)
        print(f"Chart exported: {path}")
    return result


if __name__ == "__main__":
    main()
