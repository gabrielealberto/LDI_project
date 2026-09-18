import unittest

import pandas as pd

from core.bond_cash_flow_creator import create_all_cashflows, gross_ytm
from scripts.cleaners.bond_cleaner import (
    apply_universe_filters,
    clean_fd,
    liquid_bonds,
)


class BondCashFlowTests(unittest.TestCase):
    def test_gross_ytm_for_one_year_cashflow(self):
        cashflows = pd.DataFrame(
            {
                "date": pd.to_datetime(["2030-01-01", "2031-01-01"]),
                "l1": [-1_000.0, 1_000.0],
                "l2": [0.0, 0.0],
                "l3": [0.0, 100.0],
            }
        )

        self.assertAlmostEqual(gross_ytm(cashflows), 0.1, places=12)

    def test_creates_cashflows_for_every_bond(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["A", "B"],
                "referencedate": ["01/01/2030", "01/01/2030"],
                "redemptiondate": ["01/01/2031", "01/01/2032"],
                "couponperiodicity": [0, 0],
                "currentcouponrate": [0.0, 0.0],
                "couponmonths": [None, None],
                "price": [90.0, 95.0],
            }
        )

        cashflows = create_all_cashflows(bonds)

        self.assertEqual(
            cashflows.groupby("isincode").size().to_dict(), {"A": 2, "B": 2}
        )


class BondCleanerTests(unittest.TestCase):
    def test_cleaning_includes_bbb_minus_and_excludes_lower_ratings(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["BBB", "BBB_MINUS", "BB_PLUS", "MOODYS_BAA3", "AAA"],
                "currencycode": ["EUR"] * 5,
                "issuercode": ["GOV_IT"] * 5,
                "ratingsp": ["BBB", "BBB-", "BB+", None, "AAA"],
                "ratingmoodys": [None, None, None, "Baa3", None],
                "minimumlot": [1_000] * 5,
                "pricetype": ["LP"] * 5,
                "volume": [20_000] * 5,
                "redemptiondate": ["01/01/2032"] * 5,
                "referencedate": ["01/01/2030"] * 5,
                "ratingfitch": [None] * 5,
            }
        )

        cleaned, report = clean_fd(bonds)

        self.assertEqual(
            cleaned["isincode"].tolist(),
            ["BBB", "BBB_MINUS", "MOODYS_BAA3", "AAA"],
        )
        self.assertEqual(report["rating"], 4)

    def test_rating_floor_is_inclusive_for_both_agencies(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["SP_FLOOR", "MOODYS_FLOOR", "SP_BELOW", "MOODYS_BELOW", "DISCORDANT"],
                "currencycode": ["EUR"] * 5,
                "issuercode": ["GOV_IT"] * 5,
                "ratingsp": ["BBB-", None, "BB+", None, "AAA"],
                "ratingmoodys": [None, "Baa3", None, "Ba1", "Ba1"],
                "minimumlot": [1_000] * 5,
                "pricetype": ["LP"] * 5,
                "volume": [20_000] * 5,
                "redemptiondate": ["01/01/2032"] * 5,
                "referencedate": ["01/01/2030"] * 5,
                "ratingfitch": [None] * 5,
            }
        )

        cleaned, _ = clean_fd(bonds)

        self.assertEqual(
            cleaned["isincode"].tolist(), ["SP_FLOOR", "MOODYS_FLOOR"]
        )

    def test_universe_filter_matches_effective_worst_rating(self):
        fd = pd.DataFrame(
            {
                "isincode": ["AAA_BAA3", "BBB_MINUS_AAA", "BBB_AAA"],
                "ratingsp": ["AAA", "BBB-", "BBB"],
                "ratingmoodys": ["Baa3", "AAA", "AAA"],
                "issuercode": ["GOV_IT"] * 3,
            }
        )
        bi = pd.DataFrame({"isincode": fd["isincode"]})

        filtered, filtered_bi = apply_universe_filters(
            fd, bi, allowed_ratings=["BBB-"]
        )

        self.assertEqual(filtered["isincode"].tolist(), ["AAA_BAA3", "BBB_MINUS_AAA"])
        self.assertEqual(filtered_bi["isincode"].tolist(), ["AAA_BAA3", "BBB_MINUS_AAA"])

    def test_universe_filter_intersects_rating_and_issuer_and_keeps_metadata_aligned(self):
        fd = pd.DataFrame(
            {
                "isincode": ["IT_A", "IT_B", "FR_A", "FR_B"],
                "ratingsp": ["AAA", "BBB-", "AAA", "BBB"],
                "ratingmoodys": [None] * 4,
                "issuercode": ["GOV_IT", "GOV_IT", "GOV_FR", "GOV_FR"],
            }
        )
        bi = pd.DataFrame(
            {"isincode": ["IT_A", "IT_B", "FR_A", "FR_B"], "coupon": [1, 2, 3, 4]}
        )

        filtered, filtered_bi = apply_universe_filters(
            fd,
            bi,
            allowed_ratings=["AAA", "BBB-"],
            allowed_issuers=["GOV_FR"],
        )

        self.assertEqual(filtered["isincode"].tolist(), ["FR_A"])
        self.assertEqual(filtered_bi["isincode"].tolist(), ["FR_A"])
        self.assertEqual(filtered_bi["coupon"].tolist(), [3])

    def test_per_run_universe_filters_reduce_cleaned_data(self):
        fd = pd.DataFrame(
            {
                "isincode": ["IT", "FR", "ES"],
                "ratingsp": ["AAA", "BBB", "BBB-"],
                "ratingmoodys": [None, None, None],
                "issuercode": ["GOV_IT", "GOV_FR", "GOV_ES"],
            }
        )
        bi = pd.DataFrame({"isincode": ["IT", "FR", "ES"]})

        filtered, filtered_bi = apply_universe_filters(
            fd, bi, allowed_ratings=["AAA", "BBB"], allowed_issuers=["GOV_FR"]
        )

        self.assertEqual(filtered["isincode"].tolist(), ["FR"])
        self.assertEqual(filtered_bi["isincode"].tolist(), ["FR"])

    def test_rejects_non_numeric_and_oversized_minimum_lots(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["VALID", "INVALID", "LARGE"],
                "currencycode": ["EUR"] * 3,
                "issuercode": ["GOV_IT"] * 3,
                "ratingsp": ["A"] * 3,
                "ratingmoodys": [None] * 3,
                "minimumlot": [1_000, "invalid", 2_000],
                "pricetype": ["LP"] * 3,
                "volume": [20_000] * 3,
                "redemptiondate": ["01/01/2032"] * 3,
                "referencedate": ["01/01/2030"] * 3,
                "ratingfitch": [None] * 3,
            }
        )

        cleaned, report = clean_fd(bonds)

        self.assertEqual(cleaned["isincode"].tolist(), ["VALID"])
        self.assertEqual(report["minimumlot"], 1)

    def test_requires_a_traded_price_and_sufficient_daily_volume(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["LIQUID", "REFERENCE", "THIN", "INVALID"],
                "pricetype": [" lp ", "RP", "LP", "LP"],
                "volume": [20_000, 100_000, 19_999, "invalid"],
            }
        )

        cleaned = liquid_bonds(bonds, min_daily_volume=20_000)

        self.assertEqual(cleaned["isincode"].tolist(), ["LIQUID"])

    def test_default_liquidity_threshold_requires_meaningful_daily_volume(self):
        bonds = pd.DataFrame(
            {
                "isincode": ["LIQUID", "THIN", "ZERO"],
                "pricetype": ["LP", "LP", "LP"],
                "volume": [20_000, 19_999, 0],
            }
        )

        cleaned = liquid_bonds(bonds)

        self.assertEqual(cleaned["isincode"].tolist(), ["LIQUID"])

    def test_rejects_a_negative_liquidity_threshold(self):
        bonds = pd.DataFrame({"pricetype": ["LP"], "volume": [20_000]})

        with self.assertRaisesRegex(ValueError, "finite non-negative"):
            liquid_bonds(bonds, min_daily_volume=-1)

    def test_requires_the_upstream_liquidity_columns(self):
        with self.assertRaisesRegex(ValueError, "pricetype"):
            liquid_bonds(pd.DataFrame({"volume": [20_000]}))
