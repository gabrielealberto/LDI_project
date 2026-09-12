"""Replay a frozen LDI portfolio under deterministic inflation stress paths.

This module deliberately does not re-optimise.  It measures how the portfolio
chosen on the baseline would fund the same contractual liabilities after a
coherent FOI/HICP shock.  Yield and price shocks are outside this cash-flow
stress module because they require a separate market-repricing framework.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .future_liabilities import cashflows_for_provider
from .inflation_baseline import HICP_PATH, FOI_PATH, load_history
from .inflation_linked_bonds import load_inflation_linked_bond_types
from .inflation_linked_cashflows import build_inflation_linked_cashflows
from .inflation_stress import (
    InflationShock,
    baseline_fingerprint,
    build_stressed_index_provider,
    load_inflation_stresses,
)
from .utils import (
    BOND_CASHFLOWS_PATH,
    COUPON_TAX_RATE,
    CAPITAL_GAIN_TAX_RATE,
    INFLATION_BASELINE_PATH,
    INFLATION_STRESS_MONTHLY_PATH,
    INFLATION_STRESS_SUMMARY_PATH,
    after_tax_cashflow_values,
)


def _monthly_after_tax_cashflows(cashflows: pd.DataFrame, lots: pd.Series) -> pd.Series:
    """Aggregate frozen flows using the optimizer's monthly netting convention."""
    required = {"isincode", "date", "l1", "l2", "l3"}
    if missing := required - set(cashflows):
        raise ValueError(f"Detailed cash flows are missing columns: {sorted(missing)}")
    frame = cashflows.copy()
    frame["isincode"] = frame["isincode"].astype(str)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.merge(
        lots.rename("lots"), left_on="isincode", right_index=True, how="inner"
    )
    frame["after_tax_eur"] = (
        after_tax_cashflow_values(frame, COUPON_TAX_RATE, CAPITAL_GAIN_TAX_RATE)
        * frame["lots"]
    )
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    per_isin_month = (
        frame.groupby(["isincode", "month"])["after_tax_eur"].sum().clip(lower=0)
    )
    return per_isin_month.groupby(level="month").sum().sort_index().astype(float)


def _frozen_asset_cashflows(
    portfolio: pd.DataFrame,
    bonds: pd.DataFrame,
    scenario_provider,
    base_cashflows_path: Path,
) -> pd.Series:
    """Replace only indexed positions with scenario-consistent contractual flows."""
    if portfolio.empty:
        return pd.Series(dtype=float)
    required = {"isincode", "lots"}
    if missing := required - set(portfolio):
        raise ValueError(f"Portfolio is missing columns: {sorted(missing)}")
    lots = pd.to_numeric(portfolio["lots"], errors="coerce")
    if (
        lots.isna().any()
        or not np.isfinite(lots).all()
        or not (lots > 0).all()
        or not np.all(np.equal(lots, np.rint(lots)))
    ):
        raise ValueError("Frozen portfolio lots must be positive finite integers.")
    frozen_lots = pd.Series(
        np.rint(lots).astype(int).to_numpy(),
        index=portfolio["isincode"].astype(str),
        name="lots",
    )
    if frozen_lots.index.duplicated().any():
        raise ValueError("Frozen portfolio contains duplicate ISIN positions.")
    market = bonds.copy()
    market["isincode"] = market["isincode"].astype(str)
    selected = market.loc[market["isincode"].isin(frozen_lots.index)].copy()
    missing_market = sorted(set(frozen_lots.index) - set(selected["isincode"]))
    if missing_market:
        raise ValueError(
            f"Frozen portfolio ISINs absent from bond metadata: {missing_market}"
        )

    indexed_isins = set(load_inflation_linked_bond_types())
    selected_indexed = selected.loc[selected["isincode"].isin(indexed_isins)]
    baseline_cashflows = pd.read_parquet(base_cashflows_path)
    baseline_cashflows["isincode"] = baseline_cashflows["isincode"].astype(str)
    nominal_cashflows = baseline_cashflows.loc[
        baseline_cashflows["isincode"].isin(set(frozen_lots.index) - indexed_isins)
    ].copy()
    expected_nominal = set(frozen_lots.index) - indexed_isins
    actual_nominal = set(nominal_cashflows["isincode"])
    if missing_nominal := sorted(expected_nominal - actual_nominal):
        raise ValueError(
            f"Frozen nominal positions have no detailed cash flows: {missing_nominal}"
        )
    reference_dates = (
        selected[["isincode", "referencedate"]].drop_duplicates("isincode").copy()
    )
    reference_dates["referencedate"] = pd.to_datetime(
        reference_dates["referencedate"], dayfirst=True
    )
    historical_indexed = baseline_cashflows.loc[
        baseline_cashflows["isincode"].isin(set(selected_indexed["isincode"]))
    ].merge(reference_dates, on="isincode", how="inner", validate="many_to_one")
    historical_indexed = historical_indexed.loc[
        pd.to_datetime(historical_indexed["date"])
        <= historical_indexed["referencedate"]
    ].drop(columns="referencedate")
    stressed_indexed = build_inflation_linked_cashflows(
        selected_indexed, provider=scenario_provider, require_all_terms=False
    )
    stressed_indexed = stressed_indexed.merge(
        reference_dates, on="isincode", how="inner", validate="many_to_one"
    )
    stressed_indexed = stressed_indexed.loc[
        pd.to_datetime(stressed_indexed["date"]) > stressed_indexed["referencedate"]
    ].drop(columns="referencedate")
    cashflow_parts = [
        frame
        for frame in (nominal_cashflows, historical_indexed, stressed_indexed)
        if not frame.empty
    ]
    detailed = pd.concat(cashflow_parts, ignore_index=True)
    return _monthly_after_tax_cashflows(detailed, frozen_lots)


def replay_frozen_cashflows(
    asset_cashflows: pd.Series, liability_dates, liabilities
) -> pd.DataFrame:
    """Calculate funding needed to keep a frozen portfolio cash account non-negative."""
    asset = asset_cashflows.copy()
    asset.index = pd.PeriodIndex(asset.index, freq="M").astype(str)
    liability = pd.Series(liabilities, index=pd.to_datetime(liability_dates))
    liability.index = liability.index.to_period("M").astype(str)
    liability = liability.groupby(level=0).sum().astype(float)
    all_months = sorted(set(asset.index) | set(liability.index))
    if not all_months:
        return pd.DataFrame(
            columns=[
                "month",
                "liability_eur",
                "asset_cashflow_eur",
                "pre_funding_cash_balance_eur",
                "external_cash_eur",
                "cash_balance_eur",
            ]
        )
    frame = pd.DataFrame(index=all_months)
    frame.index.name = "month"
    frame["liability_eur"] = liability.reindex(all_months, fill_value=0.0)
    frame["asset_cashflow_eur"] = asset.reindex(all_months, fill_value=0.0)
    running_cash = 0.0
    pre_funding, external, post_funding = [], [], []
    for row in frame.itertuples():
        before = running_cash + float(row.asset_cashflow_eur) - float(row.liability_eur)
        funding = max(-before, 0.0)
        running_cash = before + funding
        pre_funding.append(before)
        external.append(funding)
        post_funding.append(running_cash)
    frame["pre_funding_cash_balance_eur"] = pre_funding
    frame["external_cash_eur"] = external
    frame["cash_balance_eur"] = post_funding
    return frame.reset_index()


def run_inflation_stress_test(
    result: dict,
    bonds: pd.DataFrame,
    scenarios: list[InflationShock] | None = None,
    baseline_path: Path = INFLATION_BASELINE_PATH,
    foi_path: Path = FOI_PATH,
    hicp_path: Path = HICP_PATH,
    base_cashflows_path: Path = BOND_CASHFLOWS_PATH,
) -> dict:
    """Replay one solved portfolio under baseline plus each configured stress."""
    if "portfolio" not in result:
        raise ValueError("Optimization result must contain a portfolio.")
    baseline = pd.read_parquet(baseline_path)
    history = load_history(foi_path=foi_path, hicp_path=hicp_path)
    stresses = scenarios if scenarios is not None else load_inflation_stresses()
    base_start = pd.to_datetime(baseline["date"]).min()
    baseline_scenario = InflationShock("baseline", base_start)
    all_scenarios = [baseline_scenario, *stresses]
    if len({scenario.scenario_id for scenario in all_scenarios}) != len(all_scenarios):
        raise ValueError("Stress scenarios cannot reuse the reserved id 'baseline'.")

    summary_rows, monthly_frames, paths = [], [], {}
    fingerprint = baseline_fingerprint(baseline)
    for scenario in all_scenarios:
        provider, stressed_baseline = build_stressed_index_provider(
            baseline, history, scenario, foi_path=foi_path, hicp_path=hicp_path
        )
        dates, liabilities = cashflows_for_provider(provider)
        asset = _frozen_asset_cashflows(
            result["portfolio"], bonds, provider, Path(base_cashflows_path)
        )
        monthly = replay_frozen_cashflows(asset, dates, liabilities)
        monthly.insert(0, "scenario_id", scenario.scenario_id)
        resolved_start_date = pd.Timestamp(
            stressed_baseline["resolved_start_date"].iloc[0]
        )
        start_period = resolved_start_date.to_period("M")
        three_year_end = start_period + 35
        first_deficit = monthly.loc[
            monthly["pre_funding_cash_balance_eur"] < 0, "month"
        ]
        summary_rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "scenario_family": scenario.family,
                "severity": scenario.severity,
                "resolved_start_date": resolved_start_date,
                "effective_horizon_months": scenario.effective_horizon_months(),
                "rationale": scenario.rationale,
                "calibration_basis": scenario.calibration_basis,
                "review_frequency_months": scenario.review_frequency_months,
                "baseline_fingerprint": fingerprint,
                "model_version": scenario.model_version,
                "common_annual_shock_bp": scenario.common_annual_shock_bp,
                "foi_hicp_spread_shock_bp": scenario.foi_hicp_spread_shock_bp,
                "long_run_hicp_target": scenario.long_run_hicp_target,
                "long_run_foi_hicp_spread_bp": scenario.long_run_foi_hicp_spread_bp,
                "total_liabilities_eur": float(monthly["liability_eur"].sum()),
                "total_asset_cashflows_eur": float(monthly["asset_cashflow_eur"].sum()),
                "external_funding_eur": float(monthly["external_cash_eur"].sum()),
                "external_funding_36m_eur": float(
                    monthly.loc[
                        pd.PeriodIndex(monthly["month"], freq="M") <= three_year_end,
                        "external_cash_eur",
                    ].sum()
                ),
                "minimum_pre_funding_cash_balance_eur": float(
                    monthly["pre_funding_cash_balance_eur"].min()
                ),
                "final_cash_balance_eur": float(monthly["cash_balance_eur"].iloc[-1]),
                "deficit_months": int(
                    (monthly["pre_funding_cash_balance_eur"] < 0).sum()
                ),
                "first_deficit_month": first_deficit.iloc[0]
                if not first_deficit.empty
                else None,
                "portfolio_positions": int(len(result["portfolio"])),
                "portfolio_lots": int(result["portfolio"]["lots"].sum()),
            }
        )
        monthly_frames.append(monthly)
        paths[scenario.scenario_id] = stressed_baseline
    summary = pd.DataFrame(summary_rows)
    baseline_summary = summary.loc[summary["scenario_id"].eq("baseline")]
    if len(baseline_summary) != 1:
        raise ValueError("Stress report must contain exactly one baseline result.")
    baseline_row = baseline_summary.iloc[0]
    for column in (
        "total_liabilities_eur",
        "total_asset_cashflows_eur",
        "external_funding_eur",
        "external_funding_36m_eur",
    ):
        summary[f"delta_{column}_vs_baseline"] = summary[column] - baseline_row[column]
    return {
        "summary": summary,
        "monthly": pd.concat(monthly_frames, ignore_index=True),
        "scenario_paths": paths,
    }


def save_inflation_stress_results(
    report: dict,
    summary_path: Path = INFLATION_STRESS_SUMMARY_PATH,
    monthly_path: Path = INFLATION_STRESS_MONTHLY_PATH,
) -> tuple[Path, Path]:
    """Persist audit-ready summary and monthly stress outputs as Parquet files."""
    summary_path, monthly_path = Path(summary_path), Path(monthly_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    monthly_path.parent.mkdir(parents=True, exist_ok=True)
    report["summary"].to_parquet(summary_path, index=False, engine="pyarrow")
    report["monthly"].to_parquet(monthly_path, index=False, engine="pyarrow")
    return summary_path, monthly_path
