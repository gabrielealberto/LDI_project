"""The pre-client-report Excel export, retained for rollback and comparison."""

from pathlib import Path

import pandas as pd


def export_ldi_excel_legacy(result, output_path):
    """Export the original three-sheet workbook without presentation formatting."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(
        {
            "metric": [
                "investment_eur",
                "purchase_commission_eur",
                "sale_commission_eur",
                "eligible_bonds",
                "solver_mip_gap",
                "solver_objective",
                "total_return_eur",
                "roi",
                "annualized_return_xirr",
                "uncovered_eur",
                "weighted_average_maturity_years",
            ],
            "value": [
                result["portfolio"]["cost_eur"].sum(),
                result["purchase_commission_eur"],
                result["sale_commission_eur"],
                result["eligible_bonds"],
                result["solver_mip_gap"],
                result["solver_objective"],
                result["total_return_eur"],
                result["roi"],
                result["annualized_return"],
                result["uncovered_eur"],
                result["weighted_average_maturity_years"],
            ],
        }
    )
    with pd.ExcelWriter(output_path) as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        result["portfolio"].to_excel(writer, sheet_name="Purchase plan", index=False)
        result["cashflow_match"].to_excel(writer, sheet_name="Monthly cash flows", index=False)
    return output_path
