"""Shared project paths, mandate defaults, and small data helpers."""

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CONFIG_DIR = PROJECT_ROOT / "data" / "config"

BOND_CASHFLOWS_PATH = PROCESSED_DIR / "bond_cashflows.parquet"
BOND_CASHFLOW_MATRIX_PATH = PROCESSED_DIR / "bond_cashflow_matrix.parquet"
FD_CLEAN_PATH = PROCESSED_DIR / "fd_clean.parquet"
BI_CLEAN_PATH = PROCESSED_DIR / "bi_clean.parquet"
BONDS_PATH = FD_CLEAN_PATH
CURVE_PATH = RAW_DIR / "ecb_svensson.parquet"
INFLATION_BASELINE_PATH = PROCESSED_DIR / "inflation_baseline.parquet"
INFLATION_STRESS_SCENARIOS_PATH = CONFIG_DIR / "inflation_stress_scenarios.json"
INFLATION_STRESS_SUMMARY_PATH = PROCESSED_DIR / "inflation_stress_summary.parquet"
INFLATION_STRESS_MONTHLY_PATH = PROCESSED_DIR / "inflation_stress_monthly.parquet"

NOMINAL = 1_000
MAX_NOMINAL_PER_BOND = 50_000
COUPON_TAX_RATE = 0.125
MAX_ISSUER_WEIGHT = 0.40
MAX_POSITIONS = 30
# Set above zero to retain this share of the initial portfolio cost as terminal cash.
TERMINAL_CAPITAL_RATIO = 0.85
BROKER_FEE_RATE = 0.0019
BROKER_MIN_FEE = 2.95
BROKER_MAX_FEE = 19.00


def optional_numeric(values):
    """Parse optional numeric data while preserving invalid values as missing."""
    return pd.to_numeric(values, errors="coerce")


def after_tax_cashflow_values(cashflows, coupon_tax_rate):
    """Return principal, accrued interest, and coupon cash flows net of coupon tax."""
    return cashflows["l1"] + cashflows["l2"] + cashflows["l3"] * (1 - coupon_tax_rate)
