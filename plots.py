"""Research-grade figures for the monthly LDI portfolio."""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd

from future_liabilities import portfolio as liability_portfolio
from ldi_engine import load_bond_inputs, optimize_cashflow_matching
from utils import MAX_ISSUER_WEIGHT, PROCESSED_DIR


PLOTS_DIR = PROCESSED_DIR / "plots"
COLORS = {
    "ink": "#182633",
    "muted": "#65717C",
    "blue": "#315A7D",
    "teal": "#4F7C78",
    "amber": "#B38A4A",
    "red": "#9E5148",
    "grid": "#D8DEE1",
    "canvas": "#F3F3F0",
    "white": "#FFFFFF",
}
SOURCE_NOTE = "Source: LDI engine output. Monetary values in EUR; calculations use monthly cash flows."


def _save(figure, output_dir, name):
    """Save a print-ready raster figure and close its handle."""
    path = Path(output_dir) / name
    figure.savefig(path, dpi=240, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    return path


def _new_figure(*, figsize=(13, 8), height_ratios=(1.5, 1)):
    figure, axes = plt.subplots(
        2,
        1,
        figsize=figsize,
        facecolor=COLORS["white"],
        gridspec_kw={"height_ratios": height_ratios},
    )
    figure.subplots_adjust(left=0.09, right=0.97, top=0.81, bottom=0.13, hspace=0.42)
    return figure, axes


def _figure_header(figure, title, subtitle, insight):
    figure.text(
        0.09, 0.955, title, va="top", color=COLORS["ink"], fontsize=17, fontweight="bold"
    )
    figure.text(0.09, 0.915, subtitle, va="top", color=COLORS["muted"], fontsize=9.5)
    figure.text(
        0.09,
        0.875,
        insight,
        va="top",
        color=COLORS["teal"],
        fontsize=9.5,
        fontweight="semibold",
    )
    figure.text(0.09, 0.025, SOURCE_NOTE, color=COLORS["muted"], fontsize=7.5)


def _style_axes(*axes, grid_axis="y"):
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis=grid_axis, color=COLORS["grid"], linewidth=0.7)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.spines["left"].set_color(COLORS["grid"])
        axis.spines["bottom"].set_color(COLORS["grid"])
        axis.tick_params(colors=COLORS["muted"], labelsize=8.5)
        axis.xaxis.label.set_color(COLORS["muted"])
        axis.yaxis.label.set_color(COLORS["muted"])


def _monthly_cashflows(result):
    monthly = result["cashflow_match"].copy()
    monthly["date"] = pd.PeriodIndex(monthly["month"], freq="M").to_timestamp()
    for column in ["liability_eur", "asset_cashflow_eur", "external_cash_eur"]:
        if column not in monthly:
            monthly[column] = 0.0
    if "net_cashflow_eur" not in monthly:
        monthly["net_cashflow_eur"] = (
            monthly["asset_cashflow_eur"]
            + monthly["external_cash_eur"]
            - monthly["liability_eur"]
        )
    if "cash_balance_eur" not in monthly:
        monthly["cash_balance_eur"] = monthly["net_cashflow_eur"].cumsum()
    return monthly


def _compact_eur(value):
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"EUR {value / 1_000_000:,.2f}m"
    if absolute >= 1_000:
        return f"EUR {value / 1_000:,.1f}k"
    return f"EUR {value:,.0f}"


def _compact_axis_eur(value, _position=None):
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if absolute >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def _annotate_point(axis, x_value, y_value, text, color):
    axis.scatter([x_value], [y_value], s=30, color=color, edgecolor="white", zorder=4)
    axis.annotate(
        text,
        (x_value, y_value),
        xytext=(8, 10),
        textcoords="offset points",
        color=COLORS["ink"],
        fontsize=8,
        bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": COLORS["grid"]},
    )


def _tightest_payment_index(monthly, values):
    """Return the tightest point after a liability payment, not an idle opening month."""
    payment_months = monthly["liability_eur"] > 0
    candidates = values.loc[payment_months] if payment_months.any() else values
    return candidates.idxmin()


def _metric_card(axis, label, value, detail):
    axis.set_axis_off()
    axis.add_patch(
        FancyBboxPatch(
            (0, 0),
            1,
            1,
            boxstyle="round,pad=0.02,rounding_size=0.04",
            facecolor=COLORS["white"],
            edgecolor=COLORS["grid"],
            linewidth=0.9,
            transform=axis.transAxes,
            clip_on=False,
        )
    )
    axis.text(
        0.08,
        0.72,
        label.upper(),
        color=COLORS["muted"],
        fontsize=7.5,
        fontweight="bold",
        transform=axis.transAxes,
    )
    axis.text(
        0.08,
        0.39,
        value,
        color=COLORS["ink"],
        fontsize=16,
        fontweight="bold",
        transform=axis.transAxes,
    )
    axis.text(0.08, 0.14, detail, color=COLORS["muted"], fontsize=7, transform=axis.transAxes)


def plot_cash_account(result, output_dir):
    """Show the liquidity buffer, monthly mismatch, and external funding."""
    monthly = _monthly_cashflows(result)
    figure, (top, bottom) = _new_figure(height_ratios=(1.35, 1))
    balance = monthly["cash_balance_eur"]
    minimum_index = _tightest_payment_index(monthly, balance)
    minimum_balance = float(balance.loc[minimum_index])
    external_total = float(monthly["external_cash_eur"].sum())
    _figure_header(
        figure,
        "Liquidity resilience across the funding horizon",
        "Panel A tracks available cash after each payment; Panel B isolates timing mismatches.",
        f"Minimum post-payment buffer: {_compact_eur(minimum_balance)}; external funding: {_compact_eur(external_total)}.",
    )

    top.plot(monthly["date"], balance, color=COLORS["blue"], linewidth=2.1)
    top.fill_between(
        monthly["date"],
        0,
        balance,
        where=balance >= 0,
        color=COLORS["blue"],
        alpha=0.10,
        interpolate=True,
    )
    top.fill_between(
        monthly["date"],
        0,
        balance,
        where=balance < 0,
        color=COLORS["red"],
        alpha=0.16,
        interpolate=True,
    )
    top.axhline(0, color=COLORS["ink"], linewidth=0.8)
    _annotate_point(
        top,
        monthly.loc[minimum_index, "date"],
        minimum_balance,
        f"Post-payment minimum\n{_compact_eur(minimum_balance)}",
        COLORS["red"] if minimum_balance < 0 else COLORS["blue"],
    )
    top.set_ylabel("Cash balance")
    top.yaxis.set_major_formatter(FuncFormatter(_compact_axis_eur))
    top.text(0, 1.02, "A  Cumulative liquidity buffer", transform=top.transAxes, fontweight="bold")

    organic_net = monthly["asset_cashflow_eur"] - monthly["liability_eur"]
    bottom.bar(
        monthly["date"],
        organic_net,
        width=20,
        color=np.where(organic_net >= 0, COLORS["teal"], COLORS["amber"]),
        alpha=0.88,
    )
    funding_months = monthly["external_cash_eur"] > 1e-6
    if funding_months.any():
        bottom.scatter(
            monthly.loc[funding_months, "date"],
            monthly.loc[funding_months, "external_cash_eur"],
            marker="D",
            s=28,
            color=COLORS["red"],
            label="External funding",
            zorder=3,
        )
        bottom.legend(frameon=False, fontsize=8, loc="upper right")
    bottom.axhline(0, color=COLORS["ink"], linewidth=0.8)
    bottom.set_ylabel("Assets less liabilities")
    bottom.yaxis.set_major_formatter(FuncFormatter(_compact_axis_eur))
    bottom.text(
        0, 1.02, "B  Monthly organic surplus / shortfall", transform=bottom.transAxes, fontweight="bold"
    )
    _style_axes(top, bottom)
    return _save(figure, output_dir, "01_cash_account.png")


def plot_coverage(result, output_dir):
    """Compare cumulative funding and quantify path-dependent coverage."""
    monthly = _monthly_cashflows(result)
    cumulative_assets = monthly["asset_cashflow_eur"].cumsum()
    cumulative_liabilities = monthly["liability_eur"].cumsum()
    cumulative_funding = cumulative_assets + monthly["external_cash_eur"].cumsum()
    surplus = cumulative_funding - cumulative_liabilities
    total_liabilities = float(cumulative_liabilities.iloc[-1])
    organic_ratio = float(cumulative_assets.iloc[-1] / total_liabilities) if total_liabilities else np.nan
    trough_index = _tightest_payment_index(monthly, surplus)
    trough = float(surplus.loc[trough_index])

    figure, (top, bottom) = _new_figure(height_ratios=(1.7, 1))
    ratio_text = "n/a" if pd.isna(organic_ratio) else f"{organic_ratio:.1%}"
    _figure_header(
        figure,
        "Cash-flow coverage: level and timing",
        "Cumulative proceeds are evaluated against the schedule, not only at horizon end.",
        f"Organic horizon coverage: {ratio_text}; tightest post-payment position: {_compact_eur(trough)}.",
    )
    top.plot(
        monthly["date"], cumulative_liabilities, color=COLORS["amber"], linewidth=2, label="Liabilities"
    )
    top.plot(
        monthly["date"], cumulative_assets, color=COLORS["blue"], linewidth=2.2, label="Bond cash flows"
    )
    top.fill_between(
        monthly["date"],
        cumulative_liabilities,
        cumulative_assets,
        where=cumulative_assets >= cumulative_liabilities,
        color=COLORS["teal"],
        alpha=0.12,
        interpolate=True,
    )
    top.fill_between(
        monthly["date"],
        cumulative_liabilities,
        cumulative_assets,
        where=cumulative_assets < cumulative_liabilities,
        color=COLORS["red"],
        alpha=0.12,
        interpolate=True,
    )
    top.set_ylabel("Cumulative amount")
    top.yaxis.set_major_formatter(FuncFormatter(_compact_axis_eur))
    top.legend(frameon=False, ncol=2, loc="upper left", fontsize=8.5)
    top.text(0, 1.02, "A  Organic cash-flow accumulation", transform=top.transAxes, fontweight="bold")

    bottom.plot(monthly["date"], surplus, color=COLORS["teal"], linewidth=2)
    bottom.fill_between(
        monthly["date"], 0, surplus, where=surplus >= 0, color=COLORS["teal"], alpha=0.14
    )
    bottom.fill_between(
        monthly["date"], 0, surplus, where=surplus < 0, color=COLORS["red"], alpha=0.16
    )
    bottom.axhline(0, color=COLORS["ink"], linewidth=0.8)
    _annotate_point(
        bottom,
        monthly.loc[trough_index, "date"],
        trough,
        f"Post-payment minimum\n{_compact_eur(trough)}",
        COLORS["red"] if trough < 0 else COLORS["teal"],
    )
    bottom.set_ylabel("Funded surplus")
    bottom.yaxis.set_major_formatter(FuncFormatter(_compact_axis_eur))
    bottom.text(
        0, 1.02, "B  Surplus after external funding", transform=bottom.transAxes, fontweight="bold"
    )
    _style_axes(top, bottom)
    return _save(figure, output_dir, "02_cashflow_coverage.png")


def plot_allocation(result, output_dir):
    """Show issuer concentration and position-level implementation."""
    portfolio = result["portfolio"].copy()
    if portfolio.empty:
        raise ValueError("Cannot plot allocation for an empty portfolio")
    issuer = portfolio.get("issuercode", pd.Series("Unknown", index=portfolio.index))
    issuer = issuer.fillna("Unknown").astype(str)
    issuer_costs = portfolio.assign(_issuer=issuer).groupby("_issuer")["cost_eur"].sum()
    issuer_weights = (issuer_costs / issuer_costs.sum()).sort_values()
    max_weight = float(issuer_weights.max())
    hhi = float((issuer_weights**2).sum())
    effective_issuers = 1 / hhi if hhi else np.nan
    limit = float(result.get("max_issuer_weight", MAX_ISSUER_WEIGHT))

    figure, (left, right) = plt.subplots(
        1,
        2,
        figsize=(14, 7),
        facecolor=COLORS["white"],
        gridspec_kw={"width_ratios": [1, 1.35]},
    )
    figure.subplots_adjust(left=0.09, right=0.97, top=0.80, bottom=0.15, wspace=0.35)
    _figure_header(
        figure,
        "Portfolio concentration and implementation",
        "Issuer weights expose diversification risk; position costs show how it is implemented.",
        f"Largest issuer: {max_weight:.1%}; effective issuer count (1/HHI): {effective_issuers:.1f}.",
    )
    issuer_bars = left.barh(issuer_weights.index, issuer_weights, color=COLORS["blue"], height=0.6)
    left.axvline(
        limit,
        color=COLORS["red"],
        linestyle=(0, (4, 3)),
        linewidth=1.2,
        label=f"Mandate limit ({limit:.0%})",
    )
    left.bar_label(
        issuer_bars,
        labels=[f"{value:.1%}" for value in issuer_weights],
        padding=4,
        fontsize=8,
        color=COLORS["ink"],
    )
    left.set_xlim(0, max(limit * 1.25, max_weight * 1.22))
    left.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    left.set_xlabel("Share of invested cost")
    left.set_title("A  Issuer exposure", loc="left", fontweight="bold", fontsize=10)
    left.legend(frameon=False, fontsize=8, loc="lower right")

    labels = portfolio.get("description", portfolio.get("isincode", portfolio.index))
    position_data = portfolio.assign(_label=pd.Series(labels, index=portfolio.index).astype(str))
    position_data = position_data.sort_values("cost_eur")
    if len(position_data) > 12:
        other_cost = position_data.iloc[:-11]["cost_eur"].sum()
        other = pd.DataFrame(
            {"_label": [f"Other ({len(portfolio) - 11} positions)"], "cost_eur": [other_cost]}
        )
        position_data = pd.concat(
            [other, position_data.iloc[-11:][["_label", "cost_eur"]]], ignore_index=True
        ).sort_values("cost_eur")
    position_bars = right.barh(
        position_data["_label"], position_data["cost_eur"], color=COLORS["teal"], height=0.62
    )
    right.bar_label(
        position_bars,
        labels=[_compact_axis_eur(value) for value in position_data["cost_eur"]],
        padding=4,
        fontsize=7.5,
        color=COLORS["ink"],
    )
    right.set_xlim(0, float(position_data["cost_eur"].max()) * 1.23)
    right.set_xlabel("Invested cost")
    right.xaxis.set_major_formatter(FuncFormatter(_compact_axis_eur))
    right.set_title("B  Largest bond positions", loc="left", fontweight="bold", fontsize=10)
    right.tick_params(axis="y", labelsize=7.5)
    _style_axes(left, right, grid_axis="x")
    return _save(figure, output_dir, "03_allocation_and_concentration.png")


def plot_linkedin_summary(result, output_dir):
    """Create a polished square summary image for a project post."""
    portfolio = result["portfolio"].copy()
    monthly = _monthly_cashflows(result)
    total_investment = float(portfolio["cost_eur"].sum()) if not portfolio.empty else 0.0
    total_liabilities = float(monthly["liability_eur"].sum())
    uncovered = float(result.get("uncovered_eur", 0.0))
    coverage = 1.0 if total_liabilities == 0 else 1 - uncovered / total_liabilities
    coverage = min(max(coverage, 0.0), 1.0)
    annualized_return = result.get("annualized_return", float("nan"))
    maturity = result.get("weighted_average_maturity_years", float("nan"))

    figure = plt.figure(figsize=(10, 10), facecolor=COLORS["canvas"])
    grid = figure.add_gridspec(
        12, 12, left=0.055, right=0.945, bottom=0.045, top=0.955, hspace=1.0, wspace=0.8
    )
    header = figure.add_subplot(grid[0:2, :])
    header.set_facecolor(COLORS["ink"])
    header.set_xticks([])
    header.set_yticks([])
    for spine in header.spines.values():
        spine.set_visible(False)
    header.text(
        0.035, 0.72, "LIABILITY-DRIVEN INVESTING", color="#9ABBB6", fontsize=9,
        fontweight="bold", transform=header.transAxes
    )
    header.text(
        0.035, 0.38, "Optimized bond portfolio", color=COLORS["white"], fontsize=25,
        fontweight="bold", transform=header.transAxes
    )
    header.text(
        0.035, 0.12, "Monthly cash-flow matching under investable portfolio constraints",
        color="#D9E0E3", fontsize=10, transform=header.transAxes
    )

    return_text = "N/A" if pd.isna(annualized_return) else f"{annualized_return:.2%}"
    maturity_text = "N/A" if pd.isna(maturity) else f"{maturity:.1f} yrs"
    cards = [
        ("Investment", _compact_eur(total_investment), "including purchase costs"),
        ("Liability coverage", f"{coverage:.1%}", "after external funding"),
        ("Annualized return", return_text, "portfolio cash-flow XIRR"),
        ("Avg. maturity", maturity_text, "cost-weighted"),
    ]
    for index, (label, value, detail) in enumerate(cards):
        _metric_card(
            figure.add_subplot(grid[2:4, index * 3 : (index + 1) * 3]), label, value, detail
        )

    cashflow_axis = figure.add_subplot(grid[4:9, :8])
    cumulative_assets = monthly["asset_cashflow_eur"].cumsum()
    cumulative_liabilities = monthly["liability_eur"].cumsum()
    cashflow_axis.plot(
        monthly["date"], cumulative_assets, color=COLORS["blue"], linewidth=2.8,
        label="Bond cash flows"
    )
    cashflow_axis.plot(
        monthly["date"], cumulative_liabilities, color=COLORS["amber"], linewidth=2.4,
        label="Liabilities"
    )
    cashflow_axis.fill_between(
        monthly["date"], cumulative_liabilities, cumulative_assets, color=COLORS["teal"], alpha=0.10
    )
    cashflow_axis.set_title("Cumulative matching", loc="left", color=COLORS["ink"], fontweight="bold")
    cashflow_axis.set_ylabel("EUR", color=COLORS["muted"])
    cashflow_axis.yaxis.set_major_formatter(FuncFormatter(_compact_axis_eur))
    cashflow_axis.legend(loc="upper left", frameon=False, ncol=2, fontsize=8)
    _style_axes(cashflow_axis)

    issuer_axis = figure.add_subplot(grid[4:9, 8:])
    issuer_axis.set_title("Issuer allocation", loc="left", color=COLORS["ink"], fontweight="bold")
    if portfolio.empty:
        issuer_axis.text(
            0.5, 0.5, "No positions selected", ha="center", va="center",
            color=COLORS["muted"], transform=issuer_axis.transAxes
        )
        issuer_axis.set_axis_off()
    else:
        issuer = portfolio.get("issuercode", pd.Series("Unknown", index=portfolio.index))
        issuer = issuer.fillna("Unknown").astype(str)
        issuer_costs = portfolio.assign(_issuer=issuer).groupby("_issuer")["cost_eur"].sum()
        issuer_costs = issuer_costs.sort_values(ascending=False)
        if len(issuer_costs) > 5:
            issuer_costs = pd.concat(
                [issuer_costs.iloc[:5], pd.Series({"Other": issuer_costs.iloc[5:].sum()})]
            )
        weights = (issuer_costs / issuer_costs.sum() * 100).sort_values()
        bars = issuer_axis.barh(weights.index, weights, color=COLORS["teal"], height=0.58)
        issuer_axis.set_xlim(0, max(45, float(weights.max()) * 1.22))
        issuer_axis.set_xlabel("Share of investment", color=COLORS["muted"], fontsize=8)
        issuer_axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))
        issuer_axis.bar_label(
            bars, labels=[f"{value:.1f}%" for value in weights], padding=4,
            color=COLORS["ink"], fontsize=8, fontweight="bold"
        )
        _style_axes(issuer_axis, grid_axis="x")

    footer = figure.add_subplot(grid[9:12, :])
    footer.set_axis_off()
    footer.add_patch(
        FancyBboxPatch(
            (0, 0.12), 1, 0.82, boxstyle="round,pad=0.015,rounding_size=0.025",
            facecolor=COLORS["white"], edgecolor=COLORS["grid"], linewidth=1,
            transform=footer.transAxes
        )
    )
    highlights = [
        ("OPTIMIZATION SCOPE", "Complete eligible universe"),
        ("MATCHING APPROACH", "Monthly liability coverage"),
        ("IMPLEMENTATION", "Liquidity, concentration, lots & costs"),
    ]
    for index, (label, detail) in enumerate(highlights):
        x_position = 0.035 + index * 0.325
        footer.text(
            x_position, 0.68, label, color=COLORS["teal"], fontsize=7.5,
            fontweight="bold", transform=footer.transAxes
        )
        footer.text(
            x_position, 0.43, detail, color=COLORS["ink"], fontsize=9,
            fontweight="semibold", transform=footer.transAxes
        )
    footer.text(
        0.5, -0.02, "Python  |  Mixed-Integer Optimization  |  Fixed Income",
        ha="center", color=COLORS["muted"], fontsize=8, transform=footer.transAxes
    )
    path = Path(output_dir) / "04_linkedin_summary.png"
    figure.savefig(path, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)
    return path


def plot_portfolio(result, output_dir=PLOTS_DIR):
    """Save the LDI research figures and the square project summary."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [plot_cash_account(result, output_dir), plot_coverage(result, output_dir)]
    if not result["portfolio"].empty:
        outputs.append(plot_allocation(result, output_dir))
    outputs.append(plot_linkedin_summary(result, output_dir))
    return outputs


if __name__ == "__main__":
    dates, cashflows = liability_portfolio.merge_liabilities()
    matrix, bonds = load_bond_inputs()
    result = optimize_cashflow_matching(
        pd.DataFrame({"date": dates, "cashflow": cashflows}), matrix, bonds
    )
    for path in plot_portfolio(result):
        print(f"Chart saved: {path}")
