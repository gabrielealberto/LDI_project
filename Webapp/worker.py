"""Subprocess entry point that runs and archives the canonical LDI workflow."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.ingestion_support import atomic_to_parquet, atomic_write_json
from core.orchestration import MANIFEST_PATH, execute_with_manifest
from core.utils import (
    BOND_CASHFLOWS_PATH,
    BOND_CASHFLOW_MATRIX_PATH,
    BI_CLEAN_PATH,
    FD_CLEAN_PATH,
    INFLATION_BASELINE_PATH,
    INFLATION_STRESS_MONTHLY_PATH,
    INFLATION_STRESS_SUMMARY_PATH,
    PROCESSED_DIR,
    SELECTION_EXPLANATIONS_PATH,
)
from main import main

from .repository import RunRepository, utc_now


def _safe(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return _safe(value.item())
    return value


def _summary(result: dict) -> dict:
    portfolio = result["portfolio"]
    total_cost = float(portfolio["cost_eur"].sum()) if not portfolio.empty else 0.0
    terminal_cash = float(result.get("terminal_portfolio_cash_eur", 0.0))
    keys = (
        "uncovered_eur",
        "terminal_capital_required_eur",
        "annualized_return",
        "roi",
        "total_return_eur",
        "eligible_bonds",
        "weighted_average_maturity_years",
        "purchase_commission_eur",
        "sale_commission_eur",
        "solver_mip_gap",
        "solver_objective",
        "nominal",
        "max_nominal_per_bond",
        "coupon_tax_rate",
        "capital_gain_tax_rate",
        "max_issuer_weight",
        "max_positions",
        "prefer_short_maturity",
        "terminal_capital_ratio",
    )
    payload = {key: _safe(result.get(key)) for key in keys}
    payload.update(
        {
            "total_investment_eur": total_cost,
            "terminal_portfolio_cash_eur": terminal_cash,
            "terminal_capital_ratio_actual": (
                terminal_cash / total_cost if total_cost else None
            ),
            "positions": int(len(portfolio)),
            "solver_status": result.get("status"),
        }
    )
    return payload


def _atomic_copy(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_result(result: dict, artifacts: Path) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    for key in (
        "portfolio",
        "cashflow_match",
        "tax_breakdown",
        "selection_explanations",
    ):
        frame = result.get(key)
        if frame is not None:
            atomic_to_parquet(pd.DataFrame(frame), artifacts / f"{key}.parquet")

    stress = result.get("inflation_stress") or {}
    for key in ("summary", "monthly"):
        frame = stress.get(key)
        if frame is not None:
            atomic_to_parquet(pd.DataFrame(frame), artifacts / f"stress_{key}.parquet")
    paths = []
    for scenario_id, frame in stress.get("scenario_paths", {}).items():
        current = pd.DataFrame(frame).copy()
        if "scenario_id" in current:
            current["scenario_id"] = scenario_id
            columns = ["scenario_id"] + [
                column for column in current.columns if column != "scenario_id"
            ]
            current = current[columns]
        else:
            current.insert(0, "scenario_id", scenario_id)
        paths.append(current)
    if paths:
        atomic_to_parquet(
            pd.concat(paths, ignore_index=True), artifacts / "stress_paths.parquet"
        )

    atomic_write_json(_summary(result), artifacts / "summary.json")
    standard_outputs = {
        "ldi_optimization.xlsx": PROCESSED_DIR / "ldi_optimization.xlsx",
        "inflation_baseline.parquet": INFLATION_BASELINE_PATH,
        "bond_cashflows.parquet": BOND_CASHFLOWS_PATH,
        "bond_cashflow_matrix.parquet": BOND_CASHFLOW_MATRIX_PATH,
        "fd_clean.parquet": FD_CLEAN_PATH,
        "bi_clean.parquet": BI_CLEAN_PATH,
        "selection_explanations_pipeline.parquet": SELECTION_EXPLANATIONS_PATH,
        "inflation_stress_summary_pipeline.parquet": INFLATION_STRESS_SUMMARY_PATH,
        "inflation_stress_monthly_pipeline.parquet": INFLATION_STRESS_MONTHLY_PATH,
        "pipeline_manifest.json": MANIFEST_PATH,
    }
    for name, path in standard_outputs.items():
        _atomic_copy(Path(path), artifacts / name)


def _pipeline_run_id() -> str | None:
    if not MANIFEST_PATH.exists():
        return None
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("run_id")
    except (OSError, ValueError):
        return None


def run(run_id: str, storage_dir: Path) -> int:
    repository = RunRepository(storage_dir)
    repository.update(
        run_id,
        status="running",
        started_at=utc_now(),
        pid=os.getpid(),
        error=None,
    )
    progress_stream = sys.stdout

    def emit(message: str, level: str = "PHASE") -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        print(f"{level} | {timestamp} | {message}", file=progress_stream, flush=True)

    emit("Run accepted; initializing the execution environment", "INFO")
    try:
        request_path = repository.run_dir(run_id) / "request.json"
        request_payload = (
            json.loads(request_path.read_text(encoding="utf-8"))
            if request_path.exists()
            else {}
        )
        from core.run_config import RunParameters

        parameters = RunParameters.from_mapping(request_payload.get("parameters"))

        def configured_main(audit):
            return main(audit=audit, parameters=parameters, progress=emit)

        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                result = execute_with_manifest(configured_main)
                emit("Archiving run results and audit artifacts")
                artifacts = repository.run_dir(run_id) / "artifacts"
                _persist_result(result, artifacts)
        pipeline_run_id = _pipeline_run_id()
        repository.update(
            run_id,
            status="success",
            completed_at=utc_now(),
            pipeline_run_id=pipeline_run_id,
            error=None,
        )
        emit("Run completed; results are available", "SUCCESS")
        return 0
    except Exception as error:
        artifacts = repository.run_dir(run_id) / "artifacts"
        _atomic_copy(MANIFEST_PATH, artifacts / "pipeline_manifest.json")
        repository.update(
            run_id,
            status="error",
            completed_at=utc_now(),
            pipeline_run_id=_pipeline_run_id(),
            error=f"{type(error).__name__}: {error}",
        )
        emit(f"Run failed during execution: {type(error).__name__}: {error}", "ERROR")
        return 1


def cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--storage-dir", required=True, type=Path)
    arguments = parser.parse_args()
    return run(arguments.run_id, arguments.storage_dir)


if __name__ == "__main__":
    raise SystemExit(cli())
