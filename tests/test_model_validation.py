"""System-level validation tests for model, solver, and portfolio controls."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from core.inflation_baseline import BaselineConfig, build_baseline
from core.inflation_linked_cashflows import BaselineIndexProvider, _future_cashflows
from core.ldi_engine import (
    annualized_return,
    optimize_cashflow_matching,
    save_selection_explanations,
)
from core.yield_curve import svensson_yield


def _synthetic_inflation_history(periods=240):
    dates = pd.date_range("2005-01-01", periods=periods, freq="MS")
    month = dates.month.to_numpy()
    seasonal = np.array(
        [
            0.0015,
            0.0007,
            0.0004,
            0.0002,
            0.0,
            -0.0002,
            -0.0004,
            -0.0003,
            0.0,
            0.0002,
            0.0006,
            0.0010,
        ]
    )
    monthly = 0.02 / 12 + seasonal[month - 1]
    level = 100 * np.exp(np.cumsum(monthly))
    return pd.DataFrame({"date": dates, "foi_xt_it": level * 1.08, "hicp_xt_ea": level})


def _solver_fixture():
    bonds = pd.DataFrame(
        {
            "isincode": ["EARLY", "LATE"],
            "description": ["Early payment", "Late payment"],
            "price": [90.0, 100.0],
            "redemptiondate": ["01/01/2031", "01/01/2031"],
            "referencedate": ["01/01/2030", "01/01/2030"],
        }
    )
    matrix = pd.DataFrame(
        {"2030-01": [1_000.0, 0.0], "2030-02": [0.0, 1_000.0]},
        index=pd.Index(["EARLY", "LATE"], name="isincode"),
    )
    target = pd.DataFrame({"date": ["2030-02-01"], "cashflow": [1_000.0]})
    return target, matrix, bonds


class ModelValidationTests(unittest.TestCase):
    def test_excel_reconciliation_requires_a_certified_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "certified_bond_flows.xlsx"
        if not fixture.exists():
            self.skipTest("Certified Excel fixture is not present in the repository.")

    def test_published_bond_contract_fixture_has_valid_schedule_and_flows(self):
        history = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", "2026-03-01", freq="MS"),
                "foi_xt_it": 100.0,
                "hicp_xt_ea": 100.0,
            }
        )
        provider = BaselineIndexProvider(
            {
                "FOI_XT_IT": pd.Series(
                    history.foi_xt_it.to_numpy(), index=history.date
                ),
                "HICP_XT_EA": pd.Series(
                    history.hicp_xt_ea.to_numpy(), index=history.date
                ),
            }
        )
        term = {
            "type": "btpei",
            "nominal_per_lot": 1_000,
            "issue_date": "2025-01-15",
            "maturity_date": "2026-01-15",
            "real_annual_coupon_rate": 0.02,
            "cashflow_schedule": {
                "frequency_per_year": 2,
                "first_coupon_date": "2025-07-15",
            },
            "indexation": {
                "index_id": "HICP_XT_EA",
                "observation_lag_months": 3,
                "base_reference_date": "2025-01-15",
            },
        }
        flows = _future_cashflows(term, provider, pd.Timestamp("2025-01-01"))

        self.assertEqual(flows.iloc[-1].l1, 1_000.0)
        self.assertTrue((flows["l3"] >= 0).all())

    def test_baseline_backtest_is_finite_and_does_not_use_future_observations(self):
        history = _synthetic_inflation_history()
        cutoff = pd.Timestamp("2022-12-01")
        training = history.loc[history.date <= cutoff]
        actual = history.loc[
            (history.date > cutoff)
            & (history.date <= cutoff + pd.DateOffset(months=12))
        ]
        forecast = build_baseline(training, actual.date.iloc[-1])
        self.assertEqual(len(forecast), len(actual))
        self.assertTrue(np.isfinite(forecast[["foi_xt_it", "hicp_xt_ea"]]).all().all())
        self.assertTrue((forecast["date"] > cutoff).all())
        self.assertLess(
            np.mean(
                np.abs(forecast.hicp_xt_ea.to_numpy() - actual.hicp_xt_ea.to_numpy())
            ),
            10.0,
        )

    def test_baseline_is_monotonic_under_target_inflation_sensitivity(self):
        history = _synthetic_inflation_history()
        low = build_baseline(
            history,
            pd.Timestamp("2045-01-01"),
            BaselineConfig(annual_target=0.01),
        )
        high = build_baseline(
            history,
            pd.Timestamp("2045-01-01"),
            BaselineConfig(annual_target=0.03),
        )
        self.assertTrue((high.hicp_xt_ea >= low.hicp_xt_ea).all())

    def test_curve_sensitivity_changes_rates_without_nonfinite_values(self):
        maturities = np.array([1.0, 5.0, 20.0])
        base = svensson_yield(maturities, 0.02, -0.01, 0.005, 0.002, 1.5, 8.0)
        shocked = svensson_yield(maturities, 0.03, -0.01, 0.005, 0.002, 1.5, 8.0)
        self.assertTrue(np.isfinite(shocked).all())
        self.assertTrue((shocked > base).all())

    def test_prices_and_liability_volume_are_stable(self):
        target, matrix, bonds = _solver_fixture()
        low_price = optimize_cashflow_matching(
            target, matrix, bonds, terminal_capital_ratio=0
        )
        high_price = optimize_cashflow_matching(
            target,
            matrix,
            bonds.assign(price=matrix.index.map({"EARLY": 95.0, "LATE": 105.0})),
            terminal_capital_ratio=0,
        )
        larger = optimize_cashflow_matching(
            target.assign(cashflow=2_000.0), matrix, bonds, terminal_capital_ratio=0
        )
        self.assertTrue(
            np.isfinite(low_price["cashflow_match"]["cash_balance_eur"]).all()
        )
        self.assertTrue(
            np.isfinite(high_price["cashflow_match"]["cash_balance_eur"]).all()
        )
        self.assertGreaterEqual(
            larger["portfolio"]["lots"].sum(), low_price["portfolio"]["lots"].sum()
        )

    def test_portfolio_selection_is_non_regression_and_explained(self):
        target, matrix, bonds = _solver_fixture()
        result = optimize_cashflow_matching(
            target, matrix, bonds, terminal_capital_ratio=0
        )
        self.assertEqual(result["portfolio"].iloc[0]["isincode"], "EARLY")
        explanations = result["selection_explanations"]
        self.assertIn("selection_reason", explanations)
        self.assertIn("binding_constraints", explanations)
        self.assertTrue(explanations["selection_reason"].notna().all())

    def test_selection_explanations_are_persisted_as_a_separate_artifact(self):
        target, matrix, bonds = _solver_fixture()
        result = optimize_cashflow_matching(
            target, matrix, bonds, terminal_capital_ratio=0
        )
        with self.subTest("separate parquet"):
            output = Path(self.id().replace(".", "_") + ".parquet")
            try:
                save_selection_explanations(result, output)
                stored = pd.read_parquet(output)
            finally:
                output.unlink(missing_ok=True)
        self.assertEqual(
            list(stored.columns),
            ["isincode", "selection_reason", "binding_constraints"],
        )
        self.assertEqual(len(stored), len(result["portfolio"]))

    def test_impossible_case_reports_uncovered_amount(self):
        target, matrix, bonds = _solver_fixture()
        result = optimize_cashflow_matching(
            target, matrix, bonds, max_positions=0, terminal_capital_ratio=0
        )
        self.assertGreater(result["uncovered_eur"], 0.0)

    def test_xirr_matches_independent_npv_root(self):
        months = ["2030-01", "2031-01", "2032-01"]
        cashflows = np.array([0.0, 50.0, 1_050.0])
        actual = annualized_return(1_000.0, months, cashflows)
        start = pd.Period(months[0], freq="M").to_timestamp(how="start")
        dates = pd.PeriodIndex(months, freq="M").to_timestamp(how="end")
        years = ((dates - start) / pd.Timedelta(days=365.25)).to_numpy()
        expected = brentq(
            lambda rate: -1_000.0 + np.sum(cashflows / (1 + rate) ** years),
            -0.9999,
            10.0,
        )
        self.assertAlmostEqual(actual, expected, places=12)

    def test_solver_benchmark_is_recorded(self):
        target, matrix, bonds = _solver_fixture()
        result = optimize_cashflow_matching(
            target, matrix, bonds, terminal_capital_ratio=0
        )
        self.assertGreaterEqual(result["solver_elapsed_seconds"], 0.0)
        self.assertTrue(np.isfinite(result["solver_elapsed_seconds"]))

    def test_solver_timeout_is_reported_as_an_error(self):
        target, matrix, bonds = _solver_fixture()
        with patch(
            "core.ldi_engine.milp",
            return_value=SimpleNamespace(x=None, success=False, message="time limit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "time limit"):
                optimize_cashflow_matching(
                    target, matrix, bonds, terminal_capital_ratio=0
                )


if __name__ == "__main__":
    unittest.main()
