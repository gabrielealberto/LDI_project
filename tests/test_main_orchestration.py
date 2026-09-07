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
            patch.object(
                main,
                "plot_portfolio",
                side_effect=lambda *args, **kwargs: events.append("plots") or [],
            ),
        ):
            output = main.main()

        self.assertIs(output, result)
        self.assertEqual(events, ["optimize", "excel", "stress", "save", "plots"])
        self.assertIs(result["inflation_stress"], report)
