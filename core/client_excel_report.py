"""Client-ready Excel reporting for a solved LDI mandate.

The workbook is deliberately presentation-first while retaining the complete
purchase and monthly cash-flow audit trail in separate, filterable sheets.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

COLORS = {
    "navy": "182B3A",
    "teal": "167C80",
    "sky": "DCEFF0",
    "gold": "B98A43",
    "pale_gold": "F6EEDC",
    "green": "277A52",
    "pale_green": "E5F2EA",
    "red": "B44A4A",
    "pale_red": "F8E5E5",
    "ink": "253746",
    "muted": "667783",
    "line": "D7E0E3",
    "surface": "F5F8F8",
    "white": "FFFFFF",
}
EUR = '€ #,##0.00;[Red]-€ #,##0.00;–'
EUR0 = '€ #,##0;[Red]-€ #,##0;–'
PERCENT = "0.00%"
THIN_LINE = Side(style="thin", color=COLORS["line"])


def _title(ws, title, subtitle, end_column):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=end_column)
    cell = ws.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor=COLORS["navy"])
    cell.font = Font(name="Aptos Display", size=18, bold=True, color=COLORS["white"])
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 31
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=end_column)
    cell = ws.cell(2, 1, subtitle)
    cell.fill = PatternFill("solid", fgColor=COLORS["surface"])
    cell.font = Font(name="Aptos", size=10, italic=True, color=COLORS["muted"])
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[2].height = 22


def _section(ws, row, title, start_column=1, end_column=6):
    ws.merge_cells(start_row=row, start_column=start_column, end_row=row, end_column=end_column)
    cell = ws.cell(row, start_column, title.upper())
    cell.fill = PatternFill("solid", fgColor=COLORS["teal"])
    cell.font = Font(name="Aptos", size=10, bold=True, color=COLORS["white"])
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 20


def _header(ws, row, columns):
    for index, value in enumerate(columns, start=1):
        cell = ws.cell(row, index, value)
        cell.fill = PatternFill("solid", fgColor=COLORS["navy"])
        cell.font = Font(name="Aptos", size=10, bold=True, color=COLORS["white"])
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=Side(style="medium", color=COLORS["teal"]))
    ws.row_dimensions[row].height = 30


def _table(ws, start_row, headers, rows, name, widths, number_formats=None):
    _header(ws, start_row, headers)
    for row_index, values in enumerate(rows, start=start_row + 1):
        for column_index, value in enumerate(values, start=1):
            cell = ws.cell(row_index, column_index, value)
            cell.font = Font(name="Aptos", size=10, color=COLORS["ink"])
            cell.alignment = Alignment(vertical="center")
            cell.border = Border(bottom=THIN_LINE)
            if number_formats and column_index in number_formats:
                cell.number_format = number_formats[column_index]
    end_row = max(start_row + 1, start_row + len(rows))
    tab = Table(displayName=name, ref=f"A{start_row}:{get_column_letter(len(headers))}{end_row}")
    tab.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False,
        showRowStripes=True, showColumnStripes=False
    )
    ws.add_table(tab)
    for column_index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(column_index)].width = width
    return end_row


def _kpi(ws, label_cell, value_cell, label, value, value_format=EUR, positive=None):
    label_ref = ws[label_cell]
    label_ref.value = label.upper()
    label_ref.fill = PatternFill("solid", fgColor=COLORS["sky"])
    label_ref.font = Font(name="Aptos", size=8, bold=True, color=COLORS["muted"])
    label_ref.alignment = Alignment(horizontal="center", vertical="center")
    value_cell_ref = ws[value_cell]
    value_cell_ref.value = value
    fill = COLORS["white"]
    font_color = COLORS["ink"]
    if positive is True:
        fill, font_color = COLORS["pale_green"], COLORS["green"]
    elif positive is False:
        fill, font_color = COLORS["pale_red"], COLORS["red"]
    value_cell_ref.fill = PatternFill("solid", fgColor=fill)
    value_cell_ref.font = Font(name="Aptos Display", size=15, bold=True, color=font_color)
    value_cell_ref.alignment = Alignment(horizontal="center", vertical="center")
    value_cell_ref.number_format = value_format
    for reference in (label_ref, value_cell_ref):
        reference.border = Border(left=THIN_LINE, right=THIN_LINE, top=THIN_LINE, bottom=THIN_LINE)


def _add_line_chart(ws, source, title, anchor, height=7, width=14):
    chart = LineChart()
    chart.title = title
    chart.style = 2
    chart.y_axis.title = "EUR"
    chart.x_axis.title = "Year"
    chart.height = height
    chart.width = width
    chart.add_data(Reference(ws, min_col=source[1], max_col=source[2], min_row=source[0], max_row=source[3]), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=source[1] - 1, min_row=source[0] + 1, max_row=source[3]))
    chart.legend.position = "b"
    line_colours = (COLORS["navy"], COLORS["teal"])
    for series, colour in zip(chart.series, line_colours, strict=False):
        series.graphicalProperties.line.solidFill = colour
        series.graphicalProperties.line.width = 12_700
        series.marker.symbol = "none"
        series.smooth = False
    # Avoid a crowded time axis and rely on the legend rather than overlapping
    # point labels for a client-facing long-horizon chart.
    chart.x_axis.tickLblSkip = 2
    chart.x_axis.tickMarkSkip = 2
    ws.add_chart(chart, anchor)


def _issuer_summary(portfolio):
    issuer = portfolio.copy()
    issuer["issuer"] = issuer.get("issuerdescription", pd.Series("Unknown", index=issuer.index)).fillna("Unknown")
    summary = issuer.groupby("issuer", as_index=False).agg(
        invested_eur=("cost_eur", "sum"), positions=("isincode", "nunique")
    ).sort_values("invested_eur", ascending=False)
    total = summary["invested_eur"].sum()
    summary["weight"] = summary["invested_eur"] / total if total else 0.0
    return summary


def _annual_summary(cashflows):
    annual = cashflows.copy()
    annual["year"] = pd.PeriodIndex(annual["month"], freq="M").year
    result = annual.groupby("year", as_index=False).agg(
        liabilities_eur=("liability_eur", "sum"),
        asset_cashflows_eur=("asset_cashflow_eur", "sum"),
        external_cash_eur=("external_cash_eur", "sum"),
        net_cashflow_eur=("net_cashflow_eur", "sum"),
        year_end_cash_eur=("cash_balance_eur", "last"),
    )
    return result


def _style_sheet(ws, freeze="A4"):
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = freeze
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5


def _dashboard(workbook, result, annual, issuer, as_of):
    ws = workbook.active
    ws.title = "Client Summary"
    ws.sheet_properties.tabColor = COLORS["navy"]
    _style_sheet(ws, "A6")
    _title(
        ws,
        "LDI Portfolio",
        f"Prepared {as_of:%d %b %Y}  •  Dynamic inflation baseline",
        15,
    )
    _section(ws, 4, "Mandate outcome", 1, 10)
    portfolio = result["portfolio"]
    total_cost = float(portfolio["cost_eur"].sum())
    terminal_ratio = result["terminal_portfolio_cash_eur"] / total_cost if total_cost else 0.0
    _kpi(ws, "A5", "A6", "Investment", total_cost, EUR0)
    _kpi(ws, "C5", "C6", "Residual shortfall", result["uncovered_eur"], EUR0, result["uncovered_eur"] == 0)
    _kpi(ws, "E5", "E6", "Terminal liquidity", result["terminal_portfolio_cash_eur"], EUR0)
    _kpi(ws, "G5", "G6", "Annualised return", result["annualized_return"], PERCENT)
    _kpi(ws, "I5", "I6", "Selected positions", len(portfolio), "0")
    for column in ("A", "C", "E", "G", "I"):
        ws.column_dimensions[column].width = 18
        ws.column_dimensions[chr(ord(column) + 1)].width = 4
    ws.row_dimensions[5].height = 19
    ws.row_dimensions[6].height = 31
    _section(ws, 8, "Cash-flow resilience", 1, 8)
    annual_start = 9
    headers = ["Year", "Liabilities", "Asset cash flows", "Net cash flow", "Year-end cash"]
    rows = [
        [int(row.year), float(row.liabilities_eur), float(row.asset_cashflows_eur), float(row.net_cashflow_eur), float(row.year_end_cash_eur)]
        for row in annual.itertuples(index=False)
    ]
    end = _table(ws, annual_start, headers, rows, "DashboardAnnualCashflows", [12, 17, 18, 17, 18], {2: EUR0, 3: EUR0, 4: EUR0, 5: EUR0})
    _add_line_chart(ws, (annual_start, 2, 3, end), "Annual inflows and liabilities", "G9")
    _add_line_chart(ws, (annual_start, 5, 5, end), "Year-end liquidity buffer", "G24")
    _section(ws, end + 3, "Client-facing interpretation", 1, 10)
    statements = [
        ("Portfolio purpose", "Cash-flow matching portfolio designed to meet the configured future liability schedule."),
        ("Funding status", "No external funding is required in the solved base case; monthly coverage is managed through the cumulative cash account."),
        ("Inflation basis", "Asset and liability inflation-linked cash flows use the sole dynamic FOI/HICP baseline."),
        ("Liquidity reserve", f"Terminal projected cash is {terminal_ratio:.1%} of initial investment, against the configured terminal reserve policy."),
    ]
    for row, (label, text) in enumerate(statements, start=end + 4):
        ws.cell(row, 1, label).font = Font(name="Aptos", size=10, bold=True, color=COLORS["teal"])
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=10)
        cell = ws.cell(row, 2, text)
        cell.font = Font(name="Aptos", size=10, color=COLORS["ink"])
        cell.alignment = Alignment(wrap_text=True)
        ws.row_dimensions[row].height = 24
    return ws


def _portfolio_sheet(workbook, result):
    ws = workbook.create_sheet("Portfolio")
    ws.sheet_properties.tabColor = COLORS["teal"]
    _style_sheet(ws, "A5")
    _title(ws, "Portfolio implementation", "Recommended purchase list. Amounts include estimated purchase commission.", 13)
    portfolio = result["portfolio"].copy()
    if "ratingsp" not in portfolio:
        portfolio["ratingsp"] = ""
    if "issuerdescription" not in portfolio:
        portfolio["issuerdescription"] = ""
    display = portfolio[[
        "isincode", "description", "issuerdescription", "ratingsp", "redemptiondate", "lots", "nominal_eur", "purchase_value_eur", "purchase_commission_eur", "cost_eur", "maturity_years"
    ]].copy()
    display.columns = ["ISIN", "Instrument", "Issuer", "S&P rating", "Maturity", "Lots", "Nominal", "Market value", "Commission", "Total cost", "Maturity (years)"]
    rows = display.where(pd.notna(display), "").values.tolist()
    end = _table(
        ws, 4, list(display.columns), rows, "PurchasePlan", [15, 40, 20, 12, 14, 8, 15, 16, 14, 16, 16],
        {6: "0", 7: EUR0, 8: EUR, 9: EUR, 10: EUR, 11: "0.00"},
    )
    for row in range(5, end + 1):
        ws.cell(row, 5).number_format = "dd mmm yyyy"
    total_row = end + 2
    ws.cell(total_row, 9, "Portfolio total").font = Font(name="Aptos", size=10, bold=True, color=COLORS["navy"])
    ws.cell(total_row, 10, f"=SUM(J5:J{end})")
    ws.cell(total_row, 10).number_format = EUR
    ws.cell(total_row, 10).font = Font(name="Aptos", size=11, bold=True, color=COLORS["navy"])
    ws.cell(total_row, 10).fill = PatternFill("solid", fgColor=COLORS["pale_gold"])
    issuer = _issuer_summary(portfolio)
    start = total_row + 4
    _section(ws, start, "Issuer allocation", 1, 5)
    rows = [[row.issuer, float(row.invested_eur), float(row.weight), int(row.positions)] for row in issuer.itertuples(index=False)]
    issuer_end = _table(ws, start + 1, ["Issuer", "Investment", "Weight", "Positions"], rows, "IssuerAllocation", [25, 16, 12, 12], {2: EUR0, 3: PERCENT, 4: "0"})
    chart = BarChart()
    chart.type = "bar"
    chart.style = 2
    chart.title = "Portfolio allocation by issuer"
    chart.y_axis.title = "Issuer"
    chart.x_axis.title = "EUR"
    chart.height = 8
    chart.width = 15
    chart.add_data(Reference(ws, min_col=2, max_col=2, min_row=start + 1, max_row=issuer_end), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=start + 2, max_row=issuer_end))
    chart.legend = None
    chart.varyColors = False
    chart.series[0].graphicalProperties.solidFill = COLORS["teal"]
    chart.series[0].graphicalProperties.line.solidFill = COLORS["teal"]
    ws.add_chart(chart, "F" + str(start + 1))
    return ws


def _cashflow_sheet(workbook, result):
    ws = workbook.create_sheet("Cash Flow Profile")
    ws.sheet_properties.tabColor = COLORS["gold"]
    _style_sheet(ws, "A5")
    _title(ws, "Monthly cash-flow profile", "Asset inflows, liability outflows and the cumulative liquidity account.", 9)
    cashflows = result["cashflow_match"].copy()
    display = cashflows[["month", "liability_eur", "asset_cashflow_eur", "external_cash_eur", "net_cashflow_eur", "cash_balance_eur"]].copy()
    display.columns = ["Month", "Liabilities", "Asset cash flows", "External funding", "Net cash flow", "Cash balance"]
    rows = display.values.tolist()
    end = _table(ws, 4, list(display.columns), rows, "MonthlyCashflows", [13, 18, 18, 18, 18, 18], {2: EUR, 3: EUR, 4: EUR, 5: EUR, 6: EUR})
    ws.auto_filter.ref = f"A4:F{end}"
    return ws


def _annual_sheet(workbook, annual):
    ws = workbook.create_sheet("Annual Overview")
    ws.sheet_properties.tabColor = COLORS["sky"]
    _style_sheet(ws, "A5")
    _title(ws, "Annual cash-flow overview", "Condensed view of the monthly cash ledger used by the optimizer.", 10)
    rows = [
        [int(row.year), float(row.liabilities_eur), float(row.asset_cashflows_eur), float(row.external_cash_eur), float(row.net_cashflow_eur), float(row.year_end_cash_eur)]
        for row in annual.itertuples(index=False)
    ]
    end = _table(
        ws, 4, ["Year", "Liabilities", "Asset cash flows", "External funding", "Net cash flow", "Year-end cash"],
        rows, "AnnualCashflows", [12, 18, 20, 18, 18, 18], {2: EUR0, 3: EUR0, 4: EUR0, 5: EUR0, 6: EUR0}
    )
    _add_line_chart(ws, (4, 2, 3, end), "Annual cash-flow matching", "H4", height=8, width=15)
    _add_line_chart(ws, (4, 6, 6, end), "Liquidity account", "H22", height=8, width=15)
    return ws


def _methodology_sheet(workbook, result, as_of):
    ws = workbook.create_sheet("Methodology & Controls")
    ws.sheet_properties.tabColor = COLORS["muted"]
    _style_sheet(ws, "A5")
    _title(ws, "Methodology, mandate and disclosures", "Controls and assumptions used to prepare this report.", 8)
    _section(ws, 4, "Mandate controls", 1, 5)
    controls = [
        ("Report generation date", as_of.strftime("%d %b %Y")),
        ("Inflation baseline", "Dynamic FOI/HICP baseline"),
        ("Lot size", "EUR 1,000"),
        ("Maximum nominal per ISIN", f"EUR {result['max_nominal_per_bond']:,.0f}"),
        ("Maximum issuer concentration", f"{result['max_issuer_weight']:.0%}"),
        ("Maximum positions", str(result["max_positions"])),
        ("Coupon tax rate", f"{result['coupon_tax_rate']:.1%}"),
        ("Purchase commission", "0.19%; minimum EUR 2.95, maximum EUR 19 per order"),
        ("Solver MIP gap", f"{result['solver_mip_gap']:.4%}"),
        ("Solver status", str(result["status"])),
    ]
    end = _table(ws, 5, ["Control", "Value"], controls, "MandateControls", [34, 58])
    _section(ws, end + 3, "Report interpretation", 1, 7)
    notes = [
        "This report shows a buy-and-hold, integer-lot cash-flow matching portfolio.",
        "The cash account is cumulative: coupons and redemptions received before a liability may finance later payments.",
        "Inflation-linked asset cash flows and Italian indexed liabilities use the sole dynamic FOI/HICP baseline.",
        "Market prices are taken from the cleaned market-data parquet; reported figures are estimates and should be reconfirmed before execution.",
        "This document is an analytical portfolio report and does not constitute investment, legal or tax advice.",
    ]
    for row, note in enumerate(notes, start=end + 4):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        cell = ws.cell(row, 1, "• " + note)
        cell.font = Font(name="Aptos", size=10, color=COLORS["ink"])
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.fill = PatternFill("solid", fgColor=COLORS["surface"])
        cell.border = Border(bottom=THIN_LINE)
        ws.row_dimensions[row].height = 28
    return ws


def export_client_excel_report(result, output_path):
    """Create the polished client workbook from an immutable solved result."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    portfolio = result["portfolio"]
    if portfolio.empty:
        raise ValueError("A client report requires at least one selected portfolio position.")
    as_of = datetime.now()
    annual = _annual_summary(result["cashflow_match"])
    issuer = _issuer_summary(portfolio)
    workbook = Workbook()
    _dashboard(workbook, result, annual, issuer, as_of)
    _portfolio_sheet(workbook, result)
    _cashflow_sheet(workbook, result)
    _annual_sheet(workbook, annual)
    _methodology_sheet(workbook, result, as_of)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(output_path)
    return output_path
