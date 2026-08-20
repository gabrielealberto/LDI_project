"""Decision-useful PNG charts for the monthly LDI portfolio."""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from future_liabilities import portfolio as liability_portfolio
from ldi_engine import load_bond_inputs, optimize_cashflow_matching
from utils import PROCESSED_DIR


PLOTS_DIR = PROCESSED_DIR / "plots"
COLORS = {
    "navy": "#16324F",
    "blue": "#2F6690",
    "teal": "#3B8EA5",
    "gold": "#C99700",
    "red": "#B44C4C",
    "slate": "#4B5563",
    "grid": "#D9E2EC",
}
SERIES_COLORS = (COLORS["navy"], COLORS["blue"], COLORS["teal"], COLORS["gold"])


def _save(figure, output_dir, name):
    path = output_dir / name
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)
    return path


def _style_axes(*axes):
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis="y", color=COLORS["grid"], linewidth=0.8)
        axis.spines[["top", "right"]].set_visible(False)


def _monthly_cashflows(result):
    monthly = result["cashflow_match"].copy()
    monthly["date"] = pd.PeriodIndex(monthly["month"], freq="M").to_timestamp()
    return monthly


def plot_cash_account(result, output_dir):
    """Show the monthly cash balance and cash-flow components."""
    monthly = _monthly_cashflows(result)
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    top.plot(
        monthly["date"],
        monthly["cash_balance_eur"],
        color=COLORS["teal"],
        linewidth=2.4,
        label="Cash balance",
    )
    top.fill_between(monthly["date"], monthly["cash_balance_eur"], color=COLORS["teal"], alpha=0.16)
    top.axhline(0, color=COLORS["red"], linewidth=1)
    top.set_title("Monthly LDI cash balance")
    top.set_ylabel("EUR")
    top.legend(loc="upper left")

    bottom.bar(
        monthly["date"],
        monthly["asset_cashflow_eur"],
        width=20,
        color=COLORS["blue"],
        label="Bond cash flows",
    )
    bottom.bar(
        monthly["date"],
        -monthly["liability_eur"],
        width=20,
        color=COLORS["gold"],
        label="Liability payments",
    )
    bottom.bar(
        monthly["date"],
        monthly["external_cash_eur"],
        width=20,
        color=COLORS["red"],
        label="External funding",
    )
    bottom.plot(
        monthly["date"],
        monthly["net_cashflow_eur"],
        color=COLORS["slate"],
        linewidth=1.2,
        label="Net cash flow",
    )
    bottom.axhline(0, color=COLORS["slate"], linewidth=0.8)
    bottom.set_ylabel("Monthly movement (EUR)")
    bottom.legend(ncol=2, loc="upper left")
    _style_axes(top, bottom)
    return _save(figure, output_dir, "01_cash_account.png")


def plot_coverage(result, output_dir):
    """Compare cumulative asset flows and liabilities month by month."""
    monthly = _monthly_cashflows(result)
    cumulative_assets = monthly["asset_cashflow_eur"].cumsum()
    cumulative_liabilities = monthly["liability_eur"].cumsum()
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, height_ratios=[2, 1])
    top.plot(
        monthly["date"],
        cumulative_liabilities,
        color=COLORS["red"],
        linewidth=2,
        label="Cumulative liabilities",
    )
    top.plot(
        monthly["date"],
        cumulative_assets,
        color=COLORS["blue"],
        linewidth=2.4,
        label="Cumulative bond cash flows",
    )
    top.fill_between(
        monthly["date"], cumulative_liabilities, cumulative_assets, color=COLORS["teal"], alpha=0.24
    )
    top.set_title("Cumulative monthly cash-flow coverage")
    top.set_ylabel("EUR")
    top.legend()
    bottom.bar(monthly["date"], monthly["cash_balance_eur"], width=20, color=COLORS["teal"])
    bottom.axhline(0, color=COLORS["red"], linewidth=1)
    bottom.set_ylabel("Cash balance (EUR)")
    _style_axes(top, bottom)
    return _save(figure, output_dir, "02_cashflow_coverage.png")


def plot_allocation(result, output_dir):
    """Show concentration by issuer and the individual purchases behind it."""
    portfolio = result["portfolio"].copy()
    issuer_costs = portfolio.groupby("issuercode")["cost_eur"].sum().sort_values(ascending=False)
    figure, (left, right) = plt.subplots(
        1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1, 1.35]}
    )
    pie_colors = [SERIES_COLORS[index % len(SERIES_COLORS)] for index in range(len(issuer_costs))]
    _, _, percentages = left.pie(
        issuer_costs,
        labels=issuer_costs.index,
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 9, "color": COLORS["navy"]},
        colors=pie_colors,
    )
    for color, percentage in zip(pie_colors, percentages):
        percentage.set_color(COLORS["navy"] if color == COLORS["gold"] else "white")
        percentage.set_fontweight("semibold")
    left.set_title("Issuer concentration")
    purchases = portfolio.sort_values("cost_eur")
    right.barh(purchases["description"], purchases["cost_eur"], color=COLORS["blue"])
    right.set_title("Investment by bond")
    right.set_xlabel("EUR")
    right.tick_params(axis="y", labelsize=8)
    _style_axes(right)
    return _save(figure, output_dir, "03_allocation_and_concentration.png")


def plot_portfolio(result, output_dir=PLOTS_DIR):
    """Save the complete monthly LDI dashboard as separate PNG files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return [
        plot_cash_account(result, output_dir),
        plot_coverage(result, output_dir),
        plot_allocation(result, output_dir),
    ]


if __name__ == "__main__":
    dates, cashflows = liability_portfolio.merge_liabilities()
    matrix, bonds = load_bond_inputs()
    result = optimize_cashflow_matching(
        pd.DataFrame({"date": dates, "cashflow": cashflows}), matrix, bonds
    )
    for path in plot_portfolio(result):
        print(f"Chart saved: {path}")
