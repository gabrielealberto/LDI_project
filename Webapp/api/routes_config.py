"""Validated configuration and readiness endpoints."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Blueprint, current_app, jsonify, request

from core.future_liabilities import Liability, _payment_dates
from core.run_config import RunParameters
from core.inflation_stress import InflationShock
from core.ingestion_support import atomic_write_json
from core.orchestration import LOCK_PATH
from core.utils import CONFIG_DIR, INFLATION_BASELINE_PATH


bp = Blueprint("config", __name__, url_prefix="/api/config")
LIABILITIES_PATH = CONFIG_DIR / "liabilities.json"
SCENARIOS_PATH = CONFIG_DIR / "inflation_stress_scenarios.json"
DEFAULT_PARAMETERS = RunParameters().to_dict()


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def _ensure_not_running():
    active = current_app.extensions["run_repository"].active()
    if active is not None:
        return (
            jsonify(
                {
                    "error": (
                        f"Configuration is locked while run {active['run_id']} "
                        f"is {active['status']}."
                    )
                }
            ),
            409,
        )
    if Path(LOCK_PATH).exists():
        return (
            jsonify({"error": "Configuration is locked by the desktop pipeline."}),
            409,
        )
    return None


def _write_versioned(path: Path, payload) -> None:
    storage = Path(current_app.config["WEBAPP_STORAGE_DIR"])
    history = storage / "config_history" / path.stem
    if path.exists():
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, history / f"{stamp}.json")
    atomic_write_json(payload, path)


def load_parameters(app=None) -> RunParameters:
    """Load the web profile, falling back safely to canonical desktop defaults."""
    application = app or current_app
    path = Path(application.config["WEBAPP_STORAGE_DIR"]) / "parameters.json"
    return RunParameters.from_mapping(_read_json(path, DEFAULT_PARAMETERS))


@bp.get("/parameters")
def get_parameters():
    try:
        return jsonify({**load_parameters().to_dict(), "editable": True})
    except ValueError as error:
        return jsonify({"error": f"Stored parameters are invalid: {error}"}), 500


@bp.post("/parameters")
def set_parameters():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "The parameter payload must be a JSON object."}), 400
    try:
        parameters = RunParameters.from_mapping(data)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if request.headers.get("X-Validate-Only"):
        return jsonify({"valid": True})
    with current_app.extensions["operation_lock"]:
        if conflict := _ensure_not_running():
            return conflict
        path = Path(current_app.config["WEBAPP_STORAGE_DIR"]) / "parameters.json"
        _write_versioned(path, parameters.to_dict())
    return jsonify({**parameters.to_dict(), "editable": True})


@bp.post("/parameters/reset")
def reset_parameters():
    parameters = RunParameters()
    with current_app.extensions["operation_lock"]:
        if conflict := _ensure_not_running():
            return conflict
        path = Path(current_app.config["WEBAPP_STORAGE_DIR"]) / "parameters.json"
        _write_versioned(path, parameters.to_dict())
    return jsonify({**parameters.to_dict(), "editable": True})


@bp.get("/liabilities")
def get_liabilities():
    return jsonify(_read_json(LIABILITIES_PATH, []))


def _validate_liabilities(data) -> list[str]:
    if not isinstance(data, list) or not data:
        return ["At least one liability is required."]
    errors = []
    names = []
    for index, item in enumerate(data):
        try:
            if not isinstance(item, dict):
                raise ValueError("must be an object")
            liability = Liability(**item)
            dates = _payment_dates(liability)
            if dates.empty:
                raise ValueError("frequency must be annual or every_n_years")
            if float(liability.initial_cashflow) <= 0:
                raise ValueError("initial_cashflow must be positive")
            if not 0 <= float(liability.probability) <= 1:
                raise ValueError("probability must be between 0 and 1")
            if float(liability.inflation_rate) <= -1:
                raise ValueError("inflation_rate must be greater than -1")
            if liability.indexation:
                required = {"index_id", "base_reference_date"}
                missing = required - set(liability.indexation)
                if missing:
                    raise ValueError(f"indexation is missing {sorted(missing)}")
            names.append(str(liability.name))
        except Exception as error:
            errors.append(f"Liability {index + 1}: {error}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"Liability names must be unique: {duplicates}")
    return errors


@bp.post("/liabilities")
def set_liabilities():
    data = request.get_json(silent=True)
    errors = _validate_liabilities(data)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    if request.headers.get("X-Validate-Only"):
        return jsonify({"valid": True, "count": len(data)})
    with current_app.extensions["operation_lock"]:
        if conflict := _ensure_not_running():
            return conflict
        _write_versioned(LIABILITIES_PATH, data)
    return jsonify({"saved": len(data)})


@bp.get("/inflation/baseline")
def get_baseline():
    if not INFLATION_BASELINE_PATH.exists():
        return jsonify({"error": "Inflation baseline not found."}), 404
    try:
        frame = pd.read_parquet(INFLATION_BASELINE_PATH)
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
        columns = ["date"] + [
            name
            for name in ("foi_yoy", "hicp_yoy", "foi_xt_it", "hicp_xt_ea")
            if name in frame
        ]
        return jsonify(frame[columns].to_dict(orient="records"))
    except (OSError, ValueError, KeyError) as error:
        return jsonify({"error": f"Invalid inflation baseline: {error}"}), 500


@bp.get("/inflation/scenarios")
def get_scenarios():
    return jsonify(_read_json(SCENARIOS_PATH, []))


def _validate_scenarios(data) -> list[str]:
    if not isinstance(data, list):
        return ["Payload must be a JSON array of scenarios."]
    errors = []
    identifiers = []
    for index, item in enumerate(data):
        try:
            shock = InflationShock.from_mapping(item)
            shock.validate()
            identifiers.append(shock.scenario_id)
        except Exception as error:
            errors.append(f"Scenario {index + 1}: {error}")
    duplicates = sorted(
        {identifier for identifier in identifiers if identifiers.count(identifier) > 1}
    )
    if duplicates:
        errors.append(f"Scenario IDs must be unique: {duplicates}")
    return errors


@bp.post("/inflation/scenarios")
def set_scenarios():
    data = request.get_json(silent=True)
    errors = _validate_scenarios(data)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    if request.headers.get("X-Validate-Only"):
        return jsonify({"valid": True, "count": len(data)})
    with current_app.extensions["operation_lock"]:
        if conflict := _ensure_not_running():
            return conflict
        _write_versioned(SCENARIOS_PATH, data)
    return jsonify({"saved": len(data)})


@bp.get("/readiness")
def readiness():
    from core.utils import BOND_CASHFLOWS_PATH, BONDS_PATH

    excel_path = Path(BOND_CASHFLOWS_PATH).parent / "ldi_optimization.xlsx"
    excel_writable = True
    if excel_path.exists():
        try:
            with excel_path.open("r+b"):
                pass
        except PermissionError:
            excel_writable = False

    checks = {
        "liabilities_config": LIABILITIES_PATH.exists(),
        "scenarios_config": SCENARIOS_PATH.exists(),
        "inflation_baseline": INFLATION_BASELINE_PATH.exists(),
        "bond_cashflows": BOND_CASHFLOWS_PATH.exists(),
        "bond_metadata": BONDS_PATH.exists(),
        "excel_output_writable": excel_writable,
    }
    try:
        checks["liabilities_defined"] = not _validate_liabilities(
            _read_json(LIABILITIES_PATH, [])
        )
    except (OSError, ValueError, json.JSONDecodeError):
        checks["liabilities_defined"] = False
    return jsonify({"ready": all(checks.values()), "checks": checks})


@bp.get("/universe")
def get_universe_options():
    from core.utils import FD_CLEAN_PATH

    if not FD_CLEAN_PATH.exists():
        return jsonify({"ratings": [], "issuers": [], "editable": False})
    try:
        frame = pd.read_parquet(
            FD_CLEAN_PATH, columns=["ratingsp", "issuerdescription"]
        )
        ratings = sorted(str(value) for value in frame["ratingsp"].dropna().unique())
        issuers = sorted(
            str(value) for value in frame["issuerdescription"].dropna().unique()
        )
        return jsonify({"ratings": ratings, "issuers": issuers, "editable": False})
    except (OSError, ValueError, KeyError) as error:
        return jsonify({"error": f"Invalid bond universe: {error}"}), 500
