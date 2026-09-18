"""Read-only APIs backed by the latest successful run archive."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
from flask import Blueprint, current_app, jsonify

from .excel_export import excel_download

bp = Blueprint("results", __name__, url_prefix="/api/results")


def _repository():
    return current_app.extensions["run_repository"]


def _latest_success():
    record = _repository().latest("success")
    if record is None:
        return None, (jsonify({"error": "No completed run is available."}), 404)
    return record, None


def _artifact(record: dict, name: str):
    path = _repository().artifact(record["run_id"], name)
    if not path.exists():
        return None, (
            jsonify({"error": f"Artifact {name!r} is not available for this run."}),
            404,
        )
    return path, None


def _safe(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if hasattr(value, "item"):
        return _safe(value.item())
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    output = frame.copy()
    for column in output.columns:
        if pd.api.types.is_datetime64_any_dtype(output[column]):
            output[column] = output[column].apply(
                lambda value: value.strftime("%Y-%m-%d") if pd.notna(value) else None
            )
    return [
        {key: _safe(value) for key, value in row.items()}
        for row in output.to_dict(orient="records")
    ]


def _load_frame(name: str):
    record, error = _latest_success()
    if error:
        return None, error
    path, error = _artifact(record, name)
    if error:
        return None, error
    try:
        return pd.read_parquet(path), None
    except (OSError, ValueError) as exc:
        current_app.logger.exception("Could not read run artifact %s", path)
        return None, (jsonify({"error": f"Invalid result artifact: {exc}"}), 500)


def _latest_run_metadata(record: dict) -> dict:
    """Return safe, user-facing metadata for the explicitly loaded run."""
    repository = _repository()
    request_payload = {}
    request_path = repository.run_dir(record["run_id"]) / "request.json"
    if request_path.exists():
        try:
            request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            request_payload = {}

    summary = {}
    summary_path = repository.artifact(record["run_id"], "summary.json")
    if summary_path.exists():
        try:
            stored = json.loads(summary_path.read_text(encoding="utf-8"))
            summary = {
                key: stored.get(key)
                for key in (
                    "eligible_bonds",
                    "positions",
                    "total_investment_eur",
                    "annualized_return",
                    "solver_mip_gap",
                )
                if key in stored
            }
        except (OSError, ValueError):
            summary = {}

    return {
        "run_id": record["run_id"],
        "pipeline_run_id": record.get("pipeline_run_id"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "completed_at": record.get("completed_at"),
        "parameters": request_payload.get("parameters", {}),
        "universe_filters": request_payload.get("universe_filters", {}),
        "summary": summary,
    }


@bp.get("/available")
def available():
    record = _repository().latest("success")
    if record is None:
        return jsonify(
            {
                "results_available": False,
                "stress_available": False,
                "excel_available": False,
                "latest_run": None,
            }
        )
    run_id = record["run_id"]
    return jsonify(
        {
            "run_id": run_id,
            "results_available": _repository()
            .artifact(run_id, "summary.json")
            .exists(),
            "stress_available": _repository()
            .artifact(run_id, "stress_summary.parquet")
            .exists(),
            "excel_available": _repository()
            .artifact(run_id, "ldi_optimization.xlsx")
            .exists(),
            "latest_run": _latest_run_metadata(record),
        }
    )


@bp.get("/summary")
def summary():
    record, error = _latest_success()
    if error:
        return error
    path, error = _artifact(record, "summary.json")
    if error:
        return error
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


@bp.get("/portfolio")
def portfolio():
    frame, error = _load_frame("portfolio.parquet")
    return error or jsonify(_records(frame))


@bp.get("/cashflows/monthly")
def cashflows_monthly():
    frame, error = _load_frame("cashflow_match.parquet")
    return error or jsonify(_records(frame))


@bp.get("/cashflows/annual")
def cashflows_annual():
    frame, error = _load_frame("cashflow_match.parquet")
    if error:
        return error
    frame["year"] = pd.PeriodIndex(frame["month"], freq="M").year
    annual = frame.groupby("year", as_index=False).agg(
        liabilities_eur=("liability_eur", "sum"),
        asset_cashflows_eur=("asset_cashflow_eur", "sum"),
        external_cash_eur=("external_cash_eur", "sum"),
        net_cashflow_eur=("net_cashflow_eur", "sum"),
        year_end_cash_eur=("cash_balance_eur", "last"),
    )
    return jsonify(_records(annual))


@bp.get("/taxes")
def taxes():
    frame, error = _load_frame("tax_breakdown.parquet")
    return error or jsonify(_records(frame))


@bp.get("/issuer-allocation")
def issuer_allocation():
    frame, error = _load_frame("portfolio.parquet")
    if error:
        return error
    issuer_column = (
        "issuerdescription" if "issuerdescription" in frame else "issuercode"
    )
    frame["issuer"] = frame.get(
        issuer_column, pd.Series("Unknown", index=frame.index)
    ).fillna("Unknown")
    allocation = frame.groupby("issuer", as_index=False).agg(
        invested_eur=("cost_eur", "sum"), positions=("isincode", "nunique")
    )
    allocation = allocation.sort_values("invested_eur", ascending=False)
    total = allocation["invested_eur"].sum()
    allocation["weight"] = allocation["invested_eur"] / total if total else 0.0
    return jsonify(_records(allocation))


@bp.get("/methodology")
def methodology():
    return jsonify(
        {
            "title": "How the LDI programme works",
            "intro": (
                "The programme builds a buy-and-hold bond portfolio intended to "
                "fund a defined schedule of future liabilities. It is a decision "
                "support model: results depend on market data, contractual inputs "
                "and the selected configuration."
            ),
            "sections": [
                {
                    "title": "1. Market data and investable universe",
                    "content": (
                        "The workflow refreshes or validates archived bond market "
                        "data, the ECB yield curve, and official FOI and HICP index "
                        "histories. It cleans the bond universe and retains only "
                        "instruments with usable market and contractual data."
                    ),
                },
                {
                    "title": "2. Liability schedule",
                    "content": (
                        "Future obligations are converted into dated cash flows. "
                        "Where indexation is configured, liability amounts are "
                        "projected using the same inflation baseline used for "
                        "indexed assets."
                    ),
                },
                {
                    "title": "3. Inflation baseline",
                    "content": (
                        "FOI and HICP forecasts combine recent observed inflation, "
                        "seasonality and gradual convergence to a long-run anchor. "
                        "The approach is deterministic and designed to keep asset "
                        "and liability indexation internally consistent."
                    ),
                },
                {
                    "title": "4. Bond cash flows, taxes and costs",
                    "content": (
                        "Each eligible instrument is represented by contractual "
                        "purchase, coupon, accrued-interest and redemption cash "
                        "flows. The model applies the configured tax treatment and "
                        "broker commission schedule when comparing portfolios."
                    ),
                },
                {
                    "title": "5. Portfolio optimisation",
                    "content": (
                        "A mixed-integer optimiser selects whole bond lots. It first "
                        "minimises any external funding needed to meet monthly "
                        "liabilities, then seeks an efficient implementation subject "
                        "to concentration, position, nominal and terminal-reserve "
                        "controls."
                    ),
                },
                {
                    "title": "6. Stress testing and audit trail",
                    "content": (
                        "The selected portfolio is replayed under deterministic "
                        "inflation stress paths without re-optimisation. Each run "
                        "stores its configuration, input evidence, outputs and "
                        "solver metadata so that the result can be reviewed later."
                    ),
                },
            ],
        }
    )


@bp.get("/export/portfolio.xlsx")
def export_portfolio_table():
    """Export only the columns displayed by the Portfolio web table."""
    frame, error = _load_frame("portfolio.parquet")
    if error:
        return error
    columns = {
        "isincode": "ISIN",
        "description": "Instrument",
        "issuerdescription": "Issuer",
        "ratingsp": "S&P",
        "redemptiondate": "Maturity",
        "lots": "Lots",
        "nominal_eur": "Nominal (EUR)",
        "purchase_value_eur": "Market value (EUR)",
        "purchase_commission_eur": "Commission (EUR)",
        "cost_eur": "Total cost (EUR)",
        "maturity_years": "Maturity (years)",
    }
    table = frame.reindex(columns=columns).rename(columns=columns)
    return excel_download(table, "portfolio.xlsx", "Portfolio")
