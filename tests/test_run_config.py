import unittest

from core.run_config import RunParameters


class RunParameterTests(unittest.TestCase):
    def test_partial_mapping_inherits_canonical_defaults(self):
        parameters = RunParameters.from_mapping({"max_positions": 8})
        self.assertEqual(parameters.max_positions, 8)
        self.assertEqual(parameters.nominal, RunParameters().nominal)

    def test_rejects_unknown_non_finite_and_inconsistent_values(self):
        invalid = (
            {"not_a_parameter": 1},
            {"coupon_tax_rate": float("nan")},
            {"max_nominal_per_bond": 1_500},
            {"broker_min_fee": 20, "broker_max_fee": 10},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                RunParameters.from_mapping(values)

    def test_baseline_config_contains_the_selected_values(self):
        parameters = RunParameters.from_mapping(
            {
                "baseline_annual_target": 0.03,
                "baseline_convergence_half_life_months": 12,
                "baseline_spread_half_life_months": 24,
                "baseline_seasonal_years": 8,
            }
        )
        baseline = parameters.baseline_config()
        self.assertEqual(baseline.annual_target, 0.03)
        self.assertEqual(baseline.convergence_half_life_months, 12)
        self.assertEqual(baseline.spread_half_life_months, 24)
        self.assertEqual(baseline.seasonal_years, 8)


if __name__ == "__main__":
    unittest.main()
