import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from core.inflation_linked_bonds import load_inflation_linked_bond_types
from core.utils import after_tax_cashflow_values, optional_numeric
from core.yield_curve import load_svensson_params, svensson_yield


class UtilityContractTests(unittest.TestCase):
    def test_optional_numeric_preserves_invalid_values_as_nan(self):
        values = optional_numeric(pd.Series(["1.5", "bad", None]))

        self.assertAlmostEqual(values.iloc[0], 1.5)
        self.assertTrue(pd.isna(values.iloc[1]))
        self.assertTrue(pd.isna(values.iloc[2]))

    def test_after_tax_applies_tax_only_to_coupon_component(self):
        cashflows = pd.DataFrame(
            {"l1": [1_000.0, -1_000.0], "l2": [25.0, -25.0], "l3": [80.0, 80.0]}
        )

        values = after_tax_cashflow_values(cashflows, 0.125)

        np.testing.assert_allclose(values, [1_095.0, -955.0])


class YieldCurveContractTests(unittest.TestCase):
    def test_svensson_yield_is_vectorized_and_finite_for_positive_maturities(self):
        maturities = np.array([0.5, 5.0, 30.0])
        rates = svensson_yield(maturities, 0.02, -0.01, 0.005, 0.002, 1.5, 8.0)

        self.assertEqual(rates.shape, maturities.shape)
        self.assertTrue(np.isfinite(rates).all())

    def test_load_svensson_params_returns_the_canonical_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.parquet"
            frame = pd.DataFrame(
                {
                    "PARAMETER": ["TAU2", "BETA1", "BETA0", "TAU1", "BETA3", "BETA2"],
                    "VALUE": [8.0, -0.01, 0.02, 1.5, 0.002, 0.005],
                }
            )
            frame.to_parquet(path, index=False)

            values = load_svensson_params(path, "ignored-curve-id")

        np.testing.assert_allclose(values, [0.02, -0.01, 0.005, 0.002, 1.5, 8.0])


class ConfigurationContractTests(unittest.TestCase):
    def test_inflation_linked_catalog_has_supported_types(self):
        types = load_inflation_linked_bond_types()

        self.assertGreater(len(types), 0)
        self.assertTrue(
            set(types.values()).issubset({"btpei", "btp_italia", "btp_italia_si"})
        )
        self.assertEqual(len(types), len(set(types)))
