"""Stress-analysis endpoints backed by archived run artifacts."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from flask import Blueprint, current_app, jsonify

from .excel_export import excel_download


bp = Blueprint("stress", __name__, url_prefix="/api/stress")


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
    return [
        {key: _safe(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _load(name: str):
    repository = current_app.extensions["run_repository"]
    record = repository.latest("success")
    if record is None:
        return None, (jsonify({"error": "No completed run is available."}), 404)
    path = repository.artifact(record["run_id"], name)
    if not path.exists():
        return None, (
            jsonify({"error": f"Stress artifact {name!r} is unavailable."}),
            404,
        )
    try:
        return pd.read_parquet(path), None
    except (OSError, ValueError) as error:
        current_app.logger.exception("Could not read stress artifact %s", path)
        return None, (jsonify({"error": f"Invalid stress artifact: {error}"}), 500)


@bp.get("/summary")
def stress_summary():
    frame, error = _load("stress_summary.parquet")
    return error or jsonify(_records(frame))


@bp.get("/monthly/<scenario_id>")
def stress_monthly(scenario_id: str):
    frame, error = _load("stress_monthly.parquet")
    if error:
        return error
    filtered = frame.loc[frame["scenario_id"] == scenario_id]
    if filtered.empty:
        return jsonify({"error": f"Scenario {scenario_id!r} not found."}), 404
    return jsonify(_records(filtered))


@bp.get("/monthly")
def stress_monthly_all():
    frame, error = _load("stress_monthly.parquet")
    return error or jsonify(_records(frame))


@bp.get("/paths")
def stress_paths():
    frame, error = _load("stress_paths.parquet")
    if error:
        return error
    result = {}
    for scenario_id, path in frame.groupby("scenario_id", sort=False):
        result[str(scenario_id)] = _records(path.drop(columns="scenario_id"))
    return jsonify(result)


@bp.get("/scenarios")
def scenario_ids():
    frame, error = _load("stress_summary.parquet")
    if error:
        return error
    stress = frame.loc[frame["scenario_id"] != "baseline"]
    rows = []
    for row in stress.itertuples(index=False):
        rows.append(
            {
                "scenario_id": str(row.scenario_id),
                "family": _safe(getattr(row, "scenario_family", None)),
                "severity": _safe(getattr(row, "severity", None)),
            }
        )
    return jsonify(rows)


@bp.get("/export/summary.xlsx")
def export_stress_summary_table():
    """Export only the columns displayed by the Stress Test Summary table."""
    frame, error = _load("stress_summary.parquet")
    if error:
        return error
    table = frame.copy()
    table["Outcome"] = np.where(
        pd.to_numeric(table.get("external_funding_eur"), errors="coerce")
        .fillna(0)
        .eq(0),
        "Covered",
        "Funding needed",
    )
    columns = {
        "scenario_id": "Scenario",
        "scenario_family": "Family",
        "severity": "Severity",
        "common_annual_shock_bp": "Shock (bp)",
        "total_liabilities_eur": "Total liabilities (EUR)",
        "total_asset_cashflows_eur": "Asset CF (EUR)",
        "external_funding_eur": "External funding (EUR)",
        "minimum_pre_funding_cash_balance_eur": "Min cash (EUR)",
        "deficit_months": "Deficit months",
        "first_deficit_month": "First deficit",
        "Outcome": "Outcome",
    }
    table = table.reindex(columns=columns).rename(columns=columns)
    baseline = table["Scenario"].eq("baseline")
    table = pd.concat(
        [
            table.loc[baseline],
            table.loc[~baseline].sort_values("External funding (EUR)", ascending=False),
        ],
        ignore_index=True,
    )
    return excel_download(table, "inflation_stress_summary.xlsx", "Stress summary")
