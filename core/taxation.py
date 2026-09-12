"""Italian government-bond tax calculations and annual reporting helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _normalised_cashflows(
    cashflows: pd.DataFrame, require_date: bool = True
) -> pd.DataFrame:
    required = {"l1", "l2", "l3"}
    missing = required - set(cashflows)
    if missing:
        raise ValueError(f"Cash flows are missing columns: {sorted(missing)}")
    frame = cashflows.copy()
    if "isincode" not in frame:
        frame["isincode"] = "__single_instrument__"
    frame["isincode"] = frame["isincode"].astype(str)
    if require_date and "date" not in frame:
        raise ValueError("Cash flows are missing required column: date")
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        if frame["date"].isna().any():
            raise ValueError("Cash flows contain invalid dates.")
    return frame


def capital_gain_tax_rows(
    cashflows: pd.DataFrame,
    capital_gain_tax_rate: float,
    acquisition_costs: pd.Series | dict | None = None,
) -> pd.Series:
    """Return capital-gain tax by cash-flow row.

    The acquisition basis is allocated pro-rata across capital proceeds for
    amortising instruments.  Acquisition costs may contain per-ISIN costs such
    as allocated transaction commissions.  Negative gains are reported as no
    immediate tax credit; offsetting depends on the taxpayer's regime.
    """
    frame = _normalised_cashflows(cashflows, require_date=False)
    if not 0 <= capital_gain_tax_rate <= 1:
        raise ValueError("Capital-gain tax rate must be between 0 and 1.")
    basis = (-frame["l1"].clip(upper=0)).astype(float)
    if acquisition_costs is not None:
        extra = pd.Series(acquisition_costs, dtype=float)
        extra.index = extra.index.astype(str)
        basis = basis + np.where(
            basis > 0, frame["isincode"].map(extra).fillna(0.0), 0.0
        )
    proceeds = frame["l1"].clip(lower=0).astype(float)
    grouped = frame.assign(_basis=basis, _proceeds=proceeds).groupby(
        "isincode", sort=False
    )
    total_basis = grouped["_basis"].transform("sum")
    total_proceeds = grouped["_proceeds"].transform("sum")
    allocated_basis = np.where(
        total_proceeds > 0,
        total_basis * proceeds / total_proceeds,
        0.0,
    )
    gain = proceeds - allocated_basis
    return pd.Series(
        gain.clip(lower=0) * capital_gain_tax_rate, index=frame.index, dtype=float
    )


def tax_components(
    cashflows: pd.DataFrame,
    coupon_tax_rate: float,
    capital_gain_tax_rate: float,
    acquisition_costs: pd.Series | dict | None = None,
) -> pd.DataFrame:
    """Return row-level taxable components and taxes."""
    frame = _normalised_cashflows(cashflows)
    frame["coupon_tax"] = frame["l3"].clip(lower=0) * coupon_tax_rate
    frame["capital_gain_tax"] = capital_gain_tax_rows(
        frame, capital_gain_tax_rate, acquisition_costs
    )
    frame["tax"] = frame["coupon_tax"] + frame["capital_gain_tax"]
    return frame


def allocate_purchase_commissions(portfolio: pd.DataFrame) -> pd.Series:
    """Allocate actual order commissions pro-rata to selected ISINs."""
    required = {"isincode", "purchase_value_eur", "purchase_commission_eur"}
    missing = required - set(portfolio)
    if missing:
        raise ValueError(f"Portfolio is missing columns: {sorted(missing)}")
    frame = portfolio.copy()
    values = pd.to_numeric(frame["purchase_value_eur"], errors="coerce").fillna(0.0)
    commissions = pd.to_numeric(
        frame["purchase_commission_eur"], errors="coerce"
    ).fillna(0.0)
    total_value = float(values.sum())
    total_commission = float(commissions.sum())
    if total_value <= 0 or total_commission == 0:
        return pd.Series(0.0, index=frame["isincode"].astype(str))
    return pd.Series(
        total_commission * values.to_numpy(float) / total_value,
        index=frame["isincode"].astype(str),
        dtype=float,
    )


def annual_tax_breakdown(
    cashflows: pd.DataFrame,
    portfolio: pd.DataFrame,
    coupon_tax_rate: float,
    capital_gain_tax_rate: float,
) -> pd.DataFrame:
    """Build the annual coupon/capital-gain tax table for selected lots."""
    columns = [
        "year",
        "coupon_taxes_eur",
        "capital_gain_taxes_eur",
        "total_taxes_eur",
    ]
    if portfolio.empty:
        return pd.DataFrame(columns=columns)
    selected = _normalised_cashflows(cashflows)
    selected["isincode"] = selected["isincode"].astype(str)
    portfolio = portfolio.copy()
    portfolio["isincode"] = portfolio["isincode"].astype(str)
    lots = pd.to_numeric(portfolio["lots"], errors="coerce").fillna(0.0)
    lots_by_isin = pd.Series(lots.to_numpy(float), index=portfolio["isincode"])
    selected = selected.loc[selected["isincode"].isin(lots_by_isin.index)].copy()
    selected["lots"] = selected["isincode"].map(lots_by_isin).fillna(0.0)
    commissions = allocate_purchase_commissions(portfolio)
    commission_per_lot = commissions / lots_by_isin.replace(0.0, np.nan)
    commission_per_lot = commission_per_lot.fillna(0.0)
    components = tax_components(
        selected,
        coupon_tax_rate,
        capital_gain_tax_rate,
        acquisition_costs=commission_per_lot,
    )
    components["lots"] = selected["lots"]
    components["coupon_tax"] = components["coupon_tax"] * components["lots"]
    components["capital_gain_tax"] = components["capital_gain_tax"] * components["lots"]
    components["year"] = components["date"].dt.year.astype(int)
    annual = components.groupby("year", as_index=False).agg(
        coupon_taxes_eur=("coupon_tax", "sum"),
        capital_gain_taxes_eur=("capital_gain_tax", "sum"),
    )
    annual["total_taxes_eur"] = annual[
        ["coupon_taxes_eur", "capital_gain_taxes_eur"]
    ].sum(axis=1)
    return annual[columns].sort_values("year").reset_index(drop=True)
