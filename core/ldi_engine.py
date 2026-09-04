"""Monthly integer cash-flow matching for the LDI bond universe.

Each row of the bond matrix represents one EUR 1,000 nominal lot.  The
optimizer buys a non-negative integer number of lots and covers liabilities
through their cumulative cash balance. It minimises external funding first,
then the purchase cost; any remaining funding need is reported explicitly.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, brentq, milp
from scipy.sparse import csr_matrix, hstack, identity

from .utils import (
    BOND_CASHFLOWS_PATH,
    BONDS_PATH,
    BROKER_FEE_RATE,
    BROKER_MAX_FEE,
    BROKER_MIN_FEE,
    COUPON_TAX_RATE,
    MAX_ISSUER_WEIGHT,
    MAX_NOMINAL_PER_BOND,
    MAX_POSITIONS,
    NOMINAL,
    PROCESSED_DIR,
    TERMINAL_CAPITAL_RATIO,
    after_tax_cashflow_values,
    optional_numeric as _optional_numeric,
)

OUTPUT_PATH = PROCESSED_DIR / "ldi_optimization.xlsx"
MIP_OPTIONS = {"time_limit": 600, "mip_rel_gap": 0.01}


def after_tax_cashflow_matrix(cashflows, coupon_tax_rate=COUPON_TAX_RATE):
    """Build the monthly matrix after tax on coupons only.

    ``l1`` is principal/purchase, ``l2`` accrued interest and ``l3`` coupon.
    Italian government-bond coupon taxation is 12.5% by default.
    """
    cashflows = cashflows.copy()
    cashflows["month"] = cashflows["date"].dt.to_period("M").astype(str)
    cashflows["after_tax"] = after_tax_cashflow_values(cashflows, coupon_tax_rate)
    return cashflows.pivot_table(
        index="isincode",
        columns="month",
        values="after_tax",
        aggfunc="sum",
        fill_value=0.0,
    )


def broker_commission(order_value):
    """Return the broker fee for positive bond purchase or sale order values."""
    value = np.asarray(order_value, dtype=float)
    return np.where(
        value > 0, np.clip(value * BROKER_FEE_RATE, BROKER_MIN_FEE, BROKER_MAX_FEE), 0.0
    )


def annualized_return(initial_cost, months, cashflows):
    """XIRR of the portfolio cash flows, using monthly dates from the matrix."""
    dates = pd.PeriodIndex(months, freq="M").to_timestamp(how="end")
    start = pd.Period(months[0], freq="M").to_timestamp(how="start")
    amounts = np.concatenate([[-initial_cost], np.asarray(cashflows, dtype=float)])
    years = np.concatenate([[0.0], ((dates - start) / pd.Timedelta(days=365.25)).to_numpy()])

    def npv(rate):
        return np.sum(amounts / (1 + rate) ** years)

    try:
        return brentq(npv, -0.9999, 10.0)
    except ValueError:
        return np.nan


def load_bond_inputs(cashflows_path=BOND_CASHFLOWS_PATH, bonds_path=BONDS_PATH):
    """Load the coupon-tax-adjusted matrix and output metadata."""
    matrix = after_tax_cashflow_matrix(pd.read_parquet(cashflows_path))
    bonds = pd.read_parquet(bonds_path)
    return matrix, bonds


def target_cashflows(target, date_column="date", cashflow_column="cashflow"):
    """Convert a narrow target dataframe or Series into monthly cash flows."""
    if isinstance(target, pd.Series):
        series = target.copy()
    else:
        series = target.groupby(date_column)[cashflow_column].sum()

    series.index = pd.to_datetime(series.index).to_period("M").astype(str)
    return series.groupby(level=0).sum().astype(float).loc[lambda x: x > 0]


def _eligible_bonds(matrix, bonds, months, first_liability_month):
    """Keep only tradable bonds with finite, positive future cash flows."""
    matrix = matrix.rename(index=str)
    matrix = matrix.reindex(columns=months, fill_value=0.0)
    future_cf = matrix.to_numpy(dtype=float)
    liability_start = np.array(months) >= first_liability_month

    metadata = bonds.drop_duplicates("isincode").set_index("isincode")
    prices = _optional_numeric(metadata.reindex(matrix.index)["price"])
    eligible = (
        np.isfinite(future_cf).all(axis=1)
        & (future_cf.sum(axis=1) > 0)
        & (future_cf[:, liability_start] >= 0).all(axis=1)
        & prices.notna().to_numpy()
        & (prices.to_numpy() > 0)
    )
    return matrix.loc[eligible], metadata.reindex(matrix.index[eligible])


def optimize_cashflow_matching(
    target,
    bond_matrix,
    bonds,
    date_column="date",
    cashflow_column="cashflow",
    max_nominal_per_bond=MAX_NOMINAL_PER_BOND,
    candidate_limit=None,
    max_issuer_weight=MAX_ISSUER_WEIGHT,
    max_positions=MAX_POSITIONS,
    prefer_short_maturity=True,
    terminal_capital_ratio=TERMINAL_CAPITAL_RATIO,
):
    """Return integer bond lots, portfolio cash flows and the monthly gap.

    ``target`` is a dataframe with a date and a cash-flow column (or a Series
    indexed by date/month). ``bond_matrix`` is the output of
    ``monthly_cashflow_matrix``.  One quantity equals one EUR 1,000 nominal
    lot. Purchase commissions are included in both selection and reported
    performance. ``candidate_limit`` is retained for API compatibility but is
    ignored: the MILP is always solved on the complete eligible universe.
    """
    liabilities = target_cashflows(target, date_column, cashflow_column)
    if liabilities.empty:
        empty = pd.DataFrame(
            columns=[
                "isincode",
                "description",
                "lots",
                "nominal_eur",
                "purchase_value_eur",
                "purchase_commission_eur",
                "sale_commission_eur",
                "cost_eur",
            ]
        )
        return {
            "portfolio": empty,
            "cashflow_match": pd.DataFrame(),
            "status": "empty target",
            "roi": 0.0,
            "annualized_return": np.nan,
            "max_nominal_per_bond": max_nominal_per_bond,
            "coupon_tax_rate": COUPON_TAX_RATE,
            "max_issuer_weight": max_issuer_weight,
            "max_positions": max_positions,
            "prefer_short_maturity": prefer_short_maturity,
            "uncovered_eur": 0.0,
            "weighted_average_maturity_years": np.nan,
            "purchase_commission_eur": 0.0,
            "sale_commission_eur": 0.0,
            "eligible_bonds": 0,
            "solver_mip_gap": 0.0,
            "solver_objective": 0.0,
            "total_return_eur": 0.0,
        }

    matrix_months = pd.PeriodIndex(bond_matrix.columns, freq="M")
    months = (
        pd.period_range(
            start=matrix_months.min(),
            end=pd.Period(liabilities.index.max(), freq="M"),
            freq="M",
        )
        .astype(str)
        .tolist()
    )
    target = liabilities.reindex(months, fill_value=0.0)
    matrix, metadata = _eligible_bonds(bond_matrix, bonds, months, liabilities.index.min())
    if matrix.empty:
        raise ValueError("No eligible bonds with valid future cash flows in liability months.")

    # The negative purchase flow is paid today through the objective.  It is
    # not an operating portfolio cash flow; all later coupons/redemptions are.
    _ = candidate_limit
    purchase_costs = -matrix.clip(upper=0).sum(axis=1).to_numpy(dtype=float)
    price_costs = _optional_numeric(metadata["price"]).to_numpy() * NOMINAL / 100
    costs = np.where(purchase_costs > 0, purchase_costs, price_costs)
    full_months = months
    full_target = target
    full_cashflows = matrix.clip(lower=0).to_numpy(dtype=float).T
    cashflows = full_cashflows
    event_months = (target.to_numpy() > 0) | (cashflows > 0).any(axis=1)
    months = np.asarray(months)[event_months].tolist()
    target = target.iloc[event_months]
    cashflows = cashflows[event_months]
    n_bonds, n_months = len(matrix), len(months)

    max_lots = int(max_nominal_per_bond // NOMINAL)
    if max_lots < 1:
        raise ValueError(f"max_nominal_per_bond must be at least EUR {NOMINAL:,.0f}.")
    if not 0 <= max_issuer_weight <= 1:
        raise ValueError("max_issuer_weight must be between 0 and 1.")
    if int(max_positions) != max_positions or max_positions < 0:
        raise ValueError("max_positions must be a non-negative integer.")
    if terminal_capital_ratio < 0:
        raise ValueError("terminal_capital_ratio must be non-negative.")

    if {"redemptiondate", "referencedate"}.issubset(metadata.columns):
        redemption = pd.to_datetime(metadata["redemptiondate"], dayfirst=True)
        reference = pd.to_datetime(metadata["referencedate"], dayfirst=True)
        maturity_years = (
            ((redemption - reference) / pd.Timedelta(days=365.25)).clip(lower=0).to_numpy()
        )
    else:
        maturity_years = np.zeros(n_bonds)

    # The commission schedule has three exact regions: minimum, proportional,
    # and capped. At most one region can be active for each bond order.
    segment_bonds = []
    segment_min_lots = []
    segment_max_lots = []
    segment_lot_costs = []
    segment_fixed_fees = []
    for bond_index, unit_cost in enumerate(costs):
        low_max = min(
            max_lots,
            int(np.floor((BROKER_MIN_FEE + 1e-12) / (BROKER_FEE_RATE * unit_cost))),
        )
        proportional_max = min(
            max_lots,
            int(np.floor((BROKER_MAX_FEE + 1e-12) / (BROKER_FEE_RATE * unit_cost))),
        )
        regions = [
            (1, low_max, unit_cost, BROKER_MIN_FEE),
            (low_max + 1, proportional_max, unit_cost * (1 + BROKER_FEE_RATE), 0.0),
            (proportional_max + 1, max_lots, unit_cost, BROKER_MAX_FEE),
        ]
        for minimum, maximum, lot_cost, fixed_fee in regions:
            if minimum <= maximum:
                segment_bonds.append(bond_index)
                segment_min_lots.append(minimum)
                segment_max_lots.append(maximum)
                segment_lot_costs.append(lot_cost)
                segment_fixed_fees.append(fixed_fee)

    segment_bonds = np.asarray(segment_bonds, dtype=int)
    segment_min_lots = np.asarray(segment_min_lots, dtype=float)
    segment_max_lots = np.asarray(segment_max_lots, dtype=float)
    segment_lot_costs = np.asarray(segment_lot_costs, dtype=float)
    segment_fixed_fees = np.asarray(segment_fixed_fees, dtype=float)
    n_segments = len(segment_bonds)

    # A coupon received before a liability can finance it.  Therefore coverage
    # is assessed on the cumulative cash balance, not month by month.
    # q = integer lots in a commission region; y = active region; s = external cash.
    cumulative = np.tril(np.ones((n_months, n_months)))
    coverage_matrix = hstack(
        [
            csr_matrix(cumulative @ cashflows[:, segment_bonds]),
            csr_matrix((n_months, n_segments)),
            csr_matrix(cumulative),
        ],
        format="csr",
    )
    coverage = LinearConstraint(
        coverage_matrix,
        cumulative @ target.to_numpy(),
        np.full(n_months, np.inf),
    )
    segment_by_bond = csr_matrix(
        (
            np.ones(n_segments),
            (segment_bonds, np.arange(n_segments)),
        ),
        shape=(n_bonds, n_segments),
    )
    segment_identity = identity(n_segments, format="csr")
    constraints = [
        coverage,
        LinearConstraint(
            hstack(
                [
                    segment_identity,
                    -segment_identity.multiply(segment_max_lots),
                    csr_matrix((n_segments, n_months)),
                ]
            ),
            np.full(n_segments, -np.inf),
            np.zeros(n_segments),
        ),
        LinearConstraint(
            hstack(
                [
                    segment_identity,
                    -segment_identity.multiply(segment_min_lots),
                    csr_matrix((n_segments, n_months)),
                ]
            ),
            np.zeros(n_segments),
            np.full(n_segments, np.inf),
        ),
        LinearConstraint(
            hstack(
                [
                    csr_matrix((n_bonds, n_segments)),
                    segment_by_bond,
                    csr_matrix((n_bonds, n_months)),
                ]
            ),
            np.full(n_bonds, -np.inf),
            np.ones(n_bonds),
        ),
        LinearConstraint(
            hstack(
                [
                    csr_matrix((1, n_segments)),
                    csr_matrix(np.ones((1, n_segments))),
                    csr_matrix((1, n_months)),
                ]
            ),
            -np.inf,
            max_positions,
        ),
    ]

    if "issuercode" in metadata:
        issuer = metadata["issuercode"].fillna("UNKNOWN").to_numpy()
        for code in np.unique(issuer):
            issuer_segments = issuer[segment_bonds] == code
            lot_concentration = (
                segment_lot_costs * issuer_segments - max_issuer_weight * segment_lot_costs
            )
            fee_concentration = (
                segment_fixed_fees * issuer_segments - max_issuer_weight * segment_fixed_fees
            )
            constraints.append(
                LinearConstraint(
                    hstack(
                        [
                            csr_matrix(lot_concentration.reshape(1, -1)),
                            csr_matrix(fee_concentration.reshape(1, -1)),
                            csr_matrix((1, n_months)),
                        ]
                    ),
                    -np.inf,
                    0.0,
                )
            )

    if terminal_capital_ratio > 0:
        # Retain terminal cash from the portfolio itself, excluding any external funding.
        terminal_asset_cashflows = cashflows.sum(axis=0)[segment_bonds]
        terminal_constraint = hstack(
            [
                csr_matrix(
                    (terminal_asset_cashflows - terminal_capital_ratio * segment_lot_costs).reshape(
                        1, -1
                    )
                ),
                csr_matrix((-terminal_capital_ratio * segment_fixed_fees).reshape(1, -1)),
                csr_matrix((1, n_months)),
            ]
        )
        constraints.append(
            LinearConstraint(
                terminal_constraint,
                float(target.sum()),
                np.inf,
            )
        )

    integrality = np.concatenate([np.ones(2 * n_segments), np.zeros(n_months)])
    bounds = Bounds(
        np.zeros(2 * n_segments + n_months),
        np.concatenate([segment_max_lots, np.ones(n_segments), np.full(n_months, np.inf)]),
    )
    coverage_solution = milp(
        c=np.concatenate([np.zeros(2 * n_segments), np.ones(n_months)]),
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options=MIP_OPTIONS,
    )
    if coverage_solution.x is None or not coverage_solution.success:
        raise RuntimeError(f"Optimization not solved: {coverage_solution.message}")

    minimum_shortage = coverage_solution.x[2 * n_segments :].sum()
    shortage_row = csr_matrix(
        np.concatenate([np.zeros(2 * n_segments), np.ones(n_months)]).reshape(1, -1)
    )
    maturity_penalty = 0.01 if prefer_short_maturity else 0.0
    solution = milp(
        c=np.concatenate(
            [
                segment_lot_costs + maturity_penalty * maturity_years[segment_bonds],
                segment_fixed_fees,
                np.zeros(n_months),
            ]
        ),
        integrality=integrality,
        bounds=bounds,
        constraints=constraints
        + [LinearConstraint(shortage_row, -np.inf, minimum_shortage + 1e-7)],
        options=MIP_OPTIONS,
    )
    if solution.x is None or not solution.success:
        raise RuntimeError(f"Optimization not solved: {solution.message}")

    segment_lots = np.rint(solution.x[:n_segments]).astype(int)
    lots = np.bincount(segment_bonds, weights=segment_lots, minlength=n_bonds).astype(int)
    external_cash = solution.x[2 * n_segments :]
    selected = lots > 0
    output_columns = [
        column
        for column in [
            "description",
            "issuerdescription",
            "issuercode",
            "ratingsp",
            "ratingmoodys",
            "redemptiondate",
        ]
        if column in metadata
    ]
    portfolio = (
        metadata.loc[selected, output_columns].reset_index().rename(columns={"index": "isincode"})
    )
    portfolio["lots"] = lots[selected]
    portfolio["nominal_eur"] = portfolio["lots"] * NOMINAL
    portfolio["purchase_value_eur"] = costs[selected] * portfolio["lots"].to_numpy()
    portfolio["purchase_commission_eur"] = broker_commission(portfolio["purchase_value_eur"])
    portfolio["sale_commission_eur"] = 0.0
    portfolio["cost_eur"] = portfolio["purchase_value_eur"] + portfolio["purchase_commission_eur"]
    portfolio["maturity_years"] = maturity_years[selected]
    portfolio = portfolio.sort_values("cost_eur", ascending=False).reset_index(drop=True)

    full_assets = full_cashflows @ lots
    full_external_cash = np.zeros(len(full_months))
    full_external_cash[event_months] = external_cash
    match = pd.DataFrame(
        {
            "month": full_months,
            "liability_eur": full_target.to_numpy(),
            "asset_cashflow_eur": full_assets,
            "external_cash_eur": full_external_cash,
        }
    )
    match["net_cashflow_eur"] = (
        match["asset_cashflow_eur"] + match["external_cash_eur"] - match["liability_eur"]
    )
    match["cash_balance_eur"] = match["net_cashflow_eur"].cumsum()
    total_cost = portfolio["cost_eur"].sum()
    total_inflows = full_assets.sum()

    return {
        "portfolio": portfolio,
        "cashflow_match": match,
        "status": solution.message,
        "uncovered_eur": float(external_cash.sum()),
        "max_nominal_per_bond": max_lots * NOMINAL,
        "coupon_tax_rate": COUPON_TAX_RATE,
        "max_issuer_weight": max_issuer_weight,
        "max_positions": max_positions,
        "prefer_short_maturity": prefer_short_maturity,
        "terminal_capital_ratio": terminal_capital_ratio,
        "terminal_capital_required_eur": float(terminal_capital_ratio * total_cost),
        "terminal_portfolio_cash_eur": float(full_assets.sum() - full_target.sum()),
        "weighted_average_maturity_years": (
            np.average(portfolio["maturity_years"], weights=portfolio["cost_eur"])
            if not portfolio.empty
            else np.nan
        ),
        "purchase_commission_eur": float(portfolio["purchase_commission_eur"].sum()),
        "sale_commission_eur": float(portfolio["sale_commission_eur"].sum()),
        "eligible_bonds": n_bonds,
        "solver_mip_gap": float(solution.mip_gap),
        "solver_objective": float(solution.fun),
        "roi": total_inflows / total_cost - 1 if total_cost > 0 else np.nan,
        "total_return_eur": total_inflows - total_cost,
        "annualized_return": (
            annualized_return(total_cost, full_months, full_assets) if total_cost > 0 else np.nan
        ),
    }


def print_purchase_plan(result):
    """Print a compact, investment-ready purchase list and cash-flow summary."""
    portfolio = result["portfolio"].copy()
    total_cost = portfolio["cost_eur"].sum()
    print("\nMONTHLY LDI PURCHASE PLAN")
    print(f"Selected bonds: {len(portfolio)} | Estimated investment: EUR {total_cost:,.2f}")
    print(f"ISIN limit: EUR {result['max_nominal_per_bond']:,.0f} nominal")
    print(
        f"Coupon tax: {result['coupon_tax_rate']:.1%} | Max issuer: {result['max_issuer_weight']:.0%} | Max positions: {result['max_positions']}"
    )
    print(
        f"Weighted average maturity: {result['weighted_average_maturity_years']:.2f} years | "
        "Light preference for shorter maturities"
        if result["prefer_short_maturity"]
        else "No maturity preference"
    )
    rating_columns = [column for column in ("ratingsp", "ratingmoodys") if column in portfolio]
    if rating_columns:
        # Credit ratings are ordinal categories, so a median is more meaningful
        # than an arithmetic mean. Prefer S&P and fall back to Moody's per bond.
        rating_scale = {
            "AAA": 1,
            "AA+": 2,
            "AA": 3,
            "AA-": 4,
            "A+": 5,
            "A": 6,
            "A-": 7,
            "BBB+": 8,
            "BBB": 9,
            "BBB-": 10,
            "BB+": 11,
            "BB": 12,
            "BB-": 13,
            "B+": 14,
            "B": 15,
            "B-": 16,
            "CCC": 17,
            "CC": 18,
            "C": 19,
            "D": 20,
        }
        ratings = portfolio[rating_columns].replace({"": np.nan}).bfill(axis=1).iloc[:, 0]
        ordinal = ratings.astype(str).str.strip().str.upper().map(rating_scale)
        rated = portfolio.loc[ordinal.notna(), ["cost_eur"]].assign(rating_score=ordinal.dropna())
        if not rated.empty and rated["cost_eur"].sum() > 0:
            rated = rated.sort_values("rating_score")
            midpoint = rated["cost_eur"].sum() / 2
            median_score = float(
                rated.loc[rated["cost_eur"].cumsum().ge(midpoint), "rating_score"].iloc[0]
            )
            median_rating = min(
                rating_scale, key=lambda rating: abs(rating_scale[rating] - median_score)
            )
            print(f"Weighted median credit rating: {median_rating} ({len(rated)} rated positions)")
    print(f"Residual shortfall: EUR {result['uncovered_eur']:,.2f}")
    terminal_capital = result["terminal_portfolio_cash_eur"]
    terminal_capital_ratio = terminal_capital / total_cost if total_cost else 0.0
    print(
        f"Terminal capital: EUR {terminal_capital:,.2f} "
        f"({terminal_capital_ratio:.2%} of initial capital)"
    )
    print(
        f"Eligible universe: {result['eligible_bonds']} bonds | "
        f"Certified MIP gap: {result['solver_mip_gap']:.6%}"
    )
    print(
        f"Purchase commissions: EUR {result['purchase_commission_eur']:,.2f} | "
        f"Sale commissions: EUR {result['sale_commission_eur']:,.2f}"
    )
    print(f"Total net return: EUR {result['total_return_eur']:,.2f}")
    print(
        f"Horizon ROI: {result['roi']:.2%} | Annualized return (XIRR): {result['annualized_return']:.2%}"
    )
    if portfolio.empty:
        print("No purchase required.")
    else:
        print(
            portfolio.to_string(
                index=False,
                formatters={
                    "nominal_eur": "EUR {:,.0f}".format,
                    "cost_eur": "EUR {:,.2f}".format,
                },
            )
        )


def export_ldi_excel(result, output_path=OUTPUT_PATH):
    """Export the purchase list and complete monthly cash ledger to Excel."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(
        {
            "metric": [
                "investment_eur",
                "purchase_commission_eur",
                "sale_commission_eur",
                "eligible_bonds",
                "solver_mip_gap",
                "solver_objective",
                "total_return_eur",
                "roi",
                "annualized_return_xirr",
                "uncovered_eur",
                "weighted_average_maturity_years",
            ],
            "value": [
                result["portfolio"]["cost_eur"].sum(),
                result["purchase_commission_eur"],
                result["sale_commission_eur"],
                result["eligible_bonds"],
                result["solver_mip_gap"],
                result["solver_objective"],
                result["total_return_eur"],
                result["roi"],
                result["annualized_return"],
                result["uncovered_eur"],
                result["weighted_average_maturity_years"],
            ],
        }
    )
    with pd.ExcelWriter(output_path) as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        result["portfolio"].to_excel(writer, sheet_name="Purchase plan", index=False)
        result["cashflow_match"].to_excel(writer, sheet_name="Monthly cash flows", index=False)
    return output_path


def main():
    """Run the monthly, basic LDI workflow from locally prepared inputs."""
    from .future_liabilities import scenario_cashflows

    dates, cashflows = scenario_cashflows()
    target = pd.DataFrame({"date": dates, "cashflow": cashflows})
    matrix, bonds = load_bond_inputs()
    result = optimize_cashflow_matching(target, matrix, bonds)
    print_purchase_plan(result)
    print(f"Excel exported: {export_ldi_excel(result)}")


if __name__ == "__main__":
    main()
