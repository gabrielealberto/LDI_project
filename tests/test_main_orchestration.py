import unittest
from unittest.mock import patch

import pandas as pd

import main


class MainOrchestrationTests(unittest.TestCase):
    def test_main_runs_stress_after_optimization_and_exports_everything(self):
        events = []
        result = {
            "portfolio": pd.DataFrame(),
            "cashflow_match": pd.DataFrame(),
        }
        report = {
            "summary": pd.DataFrame(
                {
                    "scenario_id": ["baseline"],
                    "scenario_family": ["baseline"],
                    "external_funding_eur": [0.0],
                    "minimum_pre_funding_cash_balance_eur": [0.0],
                    "deficit_months": [0],
                }
            )
        }

        def optimize(*args, **kwargs):
            events.append("optimize")
            return result

        def stress(*args, **kwargs):
            events.append("stress")
            return report

        with (
            patch.object(main, "refresh_ldi_inputs", return_value=["ready"]),
            patch.object(main, "baseline_cashflows", return_value=([], [])),
            patch.object(
                main, "load_bond_inputs", return_value=(pd.DataFrame(), pd.DataFrame())
            ),
            patch.object(main.pd, "read_parquet", return_value=pd.DataFrame()),
            patch.object(main, "optimize_cashflow_matching", side_effect=optimize),
            patch.object(main, "print_purchase_plan"),
            patch.object(
                main,
                "export_ldi_excel",
                side_effect=lambda value: events.append("excel") or "report.xlsx",
            ),
            patch.object(main, "run_inflation_stress_test", side_effect=stress),
            patch.object(
                main,
                "save_inflation_stress_results",
                side_effect=lambda value: events.append("save")
                or ("summary", "monthly"),
            ),
        ):
            output = main.main()

        self.assertIs(output, result)
        self.assertEqual(events, ["optimize", "excel", "stress", "save"])
        self.assertIs(result["inflation_stress"], report)

    def test_main_reports_professional_execution_phases(self):
        phases = []
        result = {"portfolio": pd.DataFrame(), "cashflow_match": pd.DataFrame()}
        report = {
            "summary": pd.DataFrame(
                {
                    "scenario_id": ["baseline"],
                    "scenario_family": ["baseline"],
                    "external_funding_eur": [0.0],
                    "minimum_pre_funding_cash_balance_eur": [0.0],
                    "deficit_months": [0],
                }
            )
        }
        with (
            patch.object(main, "refresh_ldi_inputs", return_value=[]),
            patch.object(main, "baseline_cashflows", return_value=([], [])),
            patch.object(
                main, "load_bond_inputs", return_value=(pd.DataFrame(), pd.DataFrame())
            ),
            patch.object(main.pd, "read_parquet", return_value=pd.DataFrame()),
            patch.object(main, "optimize_cashflow_matching", return_value=result),
            patch.object(main, "save_selection_explanations", return_value="audit"),
            patch.object(main, "print_purchase_plan"),
            patch.object(main, "export_ldi_excel", return_value="report.xlsx"),
            patch.object(main, "run_inflation_stress_test", return_value=report),
            patch.object(
                main,
                "save_inflation_stress_results",
                return_value=("summary", "monthly"),
            ),
            patch.object(main, "print_inflation_stress_results"),
        ):
            main.main(progress=phases.append)

        self.assertIn("Solving the cash-flow matching portfolio", phases)
        self.assertIn("Running inflation stress scenarios", phases)
        self.assertEqual(phases[-1], "Pipeline completed successfully")
