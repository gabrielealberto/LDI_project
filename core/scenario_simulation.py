"""Ex-post inflation scenario analysis for a frozen LDI portfolio.

The module deliberately does not call the optimiser.  It takes the solved
ISIN/lot composition and replays its cash ledger under every available
FOI/HICP path, keeping current market quotes and nominal-bond cash flows
unchanged.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .future_liabilities import scenario_cashflows
from .inflation_linked_bonds import load_inflation_linked_bond_types
from .inflation_linked_cashflows import build_inflation_linked_cashflows
from .ldi_engine import after_tax_cashflow_matrix
from .utils import ACTIVE_INFLATION_SCENARIO, INFLATION_SCENARIOS_PATH


def available_scenarios(path: Path = INFLATION_SCENARIOS_PATH) -> list[str]:
    """Return scenario names in deterministic report order."""
    frame = pd.read_parquet(path, columns=["scenario"])
    preferred = ["low_inflation", "baseline", "high_inflation", "severe_inflation"]
    found = set(frame["scenario"].dropna().astype(str))
    return [name for name in preferred if name in found] + sorted(found - set(preferred))


def scenario_probabilities(path: Path = INFLATION_SCENARIOS_PATH) -> pd.DataFrame:
    """Read probability metadata, validating that it is constant per scenario."""
    frame = pd.read_parquet(path)
    scenarios = frame[["scenario"]].drop_duplicates().copy()
    if "probability" in frame and "selection_percentile" in frame:
        metadata = frame.groupby("scenario", as_index=False).agg(
            probability=("probability", "first"),
            selection_percentile=("selection_percentile", "first"),
        )
        checks = frame.groupby("scenario")["probability"].nunique()
        if (checks > 1).any():
            raise ValueError("Scenario probability metadata is not constant within a scenario.")
    else:
        # Backward-compatible fallback for an older parquet. These are the
        # same probability bands written by the current scenario generator.
        fallback = {
            "low_inflation": (0.10, 0.10),
            "baseline": (0.80, 0.50),
            "high_inflation": (0.09, 0.90),
            "severe_inflation": (0.01, 0.99),
        }
        metadata = pd.DataFrame(
            [
                {"scenario": name, "probability": fallback[name][0], "selection_percentile": fallback[name][1]}
                for name in scenarios["scenario"]
                if name in fallback
            ]
        )
    metadata["probability"] = pd.to_numeric(metadata["probability"], errors="raise")
    if (metadata["probability"] < 0).any() or metadata["probability"].sum() > 1.000001:
        raise ValueError("Scenario probabilities must be non-negative and sum to at most 100%.")
    return metadata


def _scenario_asset_matrix(base_matrix, bonds, portfolio, scenario):
    """Replace only selected inflation-linked rows with scenario cash flows."""
    selected = portfolio.loc[portfolio["lots"] > 0, ["isincode", "lots"]].copy()
    selected["isincode"] = selected["isincode"].astype(str)
    isins = selected["isincode"].tolist()
    lots = selected.set_index("isincode")["lots"]
    market = bonds.loc[bonds["isincode"].astype(str).isin(isins)].copy()
    # Build structured cash flows only for the held inflation-linked bonds.
    il = set(load_inflation_linked_bond_types())
    held_il = market.loc[market["isincode"].astype(str).isin(il & set(isins))]
    scenario_il = after_tax_cashflow_matrix(
        build_inflation_linked_cashflows(held_il, scenario=scenario, require_all_terms=False)
    ) if not held_il.empty else pd.DataFrame()
    matrix = base_matrix.reindex(index=isins, fill_value=0.0).copy()
    if not scenario_il.empty:
        matrix.loc[scenario_il.index, scenario_il.columns] = scenario_il
    return matrix, lots.reindex(matrix.index).fillna(0).to_numpy(dtype=float)


def _replay(months, asset_cashflows, liabilities):
    months = pd.Index(months.astype(str))
    assets = asset_cashflows.reindex(months, fill_value=0.0).to_numpy(dtype=float)
    target = liabilities.reindex(months, fill_value=0.0).to_numpy(dtype=float)
    balance = 0.0
    external = np.zeros(len(months))
    balances = np.zeros(len(months))
    pre_funding = np.zeros(len(months))
    for i, (asset, liability) in enumerate(zip(assets, target, strict=True)):
        balance += asset - liability
        pre_funding[i] = balance
        if balance < 0:
            external[i] = -balance
            balance = 0.0
        balances[i] = balance
    match = pd.DataFrame({
        "month": months,
        "liability_eur": target,
        "asset_cashflow_eur": assets,
        "external_cash_eur": external,
        "net_cashflow_eur": assets + external - target,
        "pre_funding_cash_balance_eur": pre_funding,
        "cash_balance_eur": balances,
    })
    return match


def run_scenario_analysis(
    base_result: dict,
    bond_matrix: pd.DataFrame,
    bonds: pd.DataFrame,
    base_scenario: str = ACTIVE_INFLATION_SCENARIO,
    scenarios: list[str] | None = None,
) -> dict:
    """Replay a frozen portfolio under all selected inflation scenarios."""
    names = scenarios or available_scenarios()
    if base_scenario not in names:
        names = [base_scenario, *names]
    metadata = scenario_probabilities().set_index("scenario")
    outputs = {}
    for name in names:
        dates, liability_values = scenario_cashflows(name)
        liabilities = pd.Series(liability_values, index=pd.to_datetime(dates).to_period("M").astype(str))
        matrix, lots = _scenario_asset_matrix(bond_matrix, bonds, base_result["portfolio"], name)
        asset = matrix.clip(lower=0).mul(lots, axis=0).sum(axis=0)
        # Keep exactly the solved mandate horizon; the technical bond matrix
        # may contain historical columns that must not enter the report.
        base_months = pd.Index(base_result["cashflow_match"]["month"].astype(str))
        months = base_months
        match = _replay(months, asset, liabilities)
        info = metadata.loc[name] if name in metadata.index else pd.Series(dtype=float)
        outputs[name] = {
            "cashflow_match": match,
            "probability": float(info.get("probability", np.nan)),
            "selection_percentile": float(info.get("selection_percentile", np.nan)),
            "total_liabilities_eur": float(match["liability_eur"].sum()),
            "total_asset_cashflows_eur": float(match["asset_cashflow_eur"].sum()),
            "external_funding_eur": float(match["external_cash_eur"].sum()),
            "minimum_cash_balance_eur": float(match["pre_funding_cash_balance_eur"].min()),
            "final_cash_balance_eur": float(match["cash_balance_eur"].iloc[-1]),
            "deficit_months": int((match["external_cash_eur"] > 1e-8).sum()),
        }
    return {"base_scenario": base_scenario, "scenarios": outputs}
