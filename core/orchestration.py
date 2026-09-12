"""Production execution guardrails for the LDI workflow."""

from __future__ import annotations

import os
import json
import inspect
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .ingestion_support import atomic_write_json
from .utils import PROCESSED_DIR


LOCK_PATH = PROCESSED_DIR / ".ldi-run.lock"
MANIFEST_PATH = PROCESSED_DIR / "run_manifest.json"
MANIFEST_ARCHIVE_DIR = PROCESSED_DIR / "run_manifests"


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> dict[str, str | bool | None]:
    """Return best-effort source revision metadata without requiring Git."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "working_tree_dirty": None}
    return {"commit": commit, "working_tree_dirty": bool(status.strip())}


def _json_value(value):
    """Convert common pandas/numpy values into manifest-safe JSON values."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


class RunAudit:
    """Collect the minimum provenance needed to reproduce one workflow run."""

    def __init__(self, started: datetime | None = None):
        started = started or datetime.now(timezone.utc)
        self.run_id = f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        self.started = started
        self.inputs = {}
        self.configuration = {}
        self.warnings = []
        self.fallbacks = []
        self.stages = []
        self.solver = {}
        self.outputs = {}

    def record_stage(self, stage: str) -> None:
        if stage not in self.stages:
            self.stages.append(stage)

    def record_input(self, name: str, path: Path, **metadata) -> None:
        path = Path(path)
        if not path.exists():
            self.warnings.append(
                {"code": "MISSING_INPUT", "input": name, "path": str(path)}
            )
            return
        entry = {
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        try:
            entry["rows"] = len(pd.read_parquet(path))
        except (OSError, ValueError, ImportError):
            pass
        entry.update(_json_value(metadata))
        self.inputs[name] = entry

    def record_configuration(self, name: str, path: Path) -> None:
        path = Path(path)
        if not path.exists():
            self.warnings.append(
                {"code": "MISSING_CONFIGURATION", "name": name, "path": str(path)}
            )
            return
        self.configuration[name] = {
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "content": _json_value(json.loads(path.read_text(encoding="utf-8"))),
        }

    def record_fallback(self, dataset: str, **metadata) -> None:
        self.fallbacks.append({"dataset": dataset, **_json_value(metadata)})

    def record_solver(self, result: dict) -> None:
        keys = (
            "status",
            "solver_mip_gap",
            "solver_objective",
            "eligible_bonds",
            "uncovered_eur",
            "annualized_return",
            "roi",
            "total_return_eur",
            "purchase_commission_eur",
            "sale_commission_eur",
            "weighted_average_maturity_years",
            "terminal_capital_ratio",
            "coupon_tax_rate",
            "capital_gain_tax_rate",
            "solver_elapsed_seconds",
            "max_positions",
            "max_issuer_weight",
        )
        self.solver.update(
            {key: _json_value(result[key]) for key in keys if key in result}
        )
        portfolio = result.get("portfolio")
        if portfolio is not None:
            self.solver.update(
                {
                    "positions": int(len(portfolio)),
                    "portfolio_cost_eur": _json_value(portfolio["cost_eur"].sum())
                    if "cost_eur" in portfolio
                    else None,
                }
            )

    def record_output(self, name: str, path: Path) -> None:
        path = Path(path)
        if path.exists():
            self.outputs[name] = {
                "path": str(path),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }

    def as_dict(self, status: str = "running", **extra) -> dict:
        payload = {
            "run_id": self.run_id,
            "status": status,
            "started_at": self.started.isoformat(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "application": {
                "python_version": sys.version.split()[0],
                **_git_revision(),
            },
            "inputs": self.inputs,
            "configuration": self.configuration,
            "warnings": self.warnings,
            "fallbacks": self.fallbacks,
            "stages": self.stages,
            "solver": self.solver,
            "outputs": self.outputs,
        }
        payload.update(_json_value(extra))
        return payload


class RunLock:
    """A cross-platform exclusive lock for scheduled runs."""

    def __init__(self, path: Path = LOCK_PATH):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("x", encoding="utf-8")
        except FileExistsError as error:
            raise RuntimeError(
                f"Another LDI run is active; remove {self.path} only after verifying it is stale."
            ) from error
        self.handle.write(f"pid={os.getpid()}\nhost={socket.gethostname()}\n")
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is not None:
            self.handle.close()
        self.path.unlink(missing_ok=True)


def execute_with_manifest(
    workflow,
    manifest_path: Path = MANIFEST_PATH,
    lock_path: Path = LOCK_PATH,
):
    """Run a workflow under a lock and persist an auditable execution manifest."""
    started = datetime.now(timezone.utc)
    audit = RunAudit(started)
    with RunLock(lock_path):
        try:
            atomic_write_json(audit.as_dict(), manifest_path)
            # Preserve the zero-argument callback contract used by callers.
            if inspect.signature(workflow).parameters:
                result = workflow(audit)
            else:
                result = workflow()
            completed = datetime.now(timezone.utc)
            audit.stages = result if isinstance(result, list) else audit.stages
            manifest = audit.as_dict(
                "succeeded",
                completed_at=completed.isoformat(),
                duration_seconds=round((completed - started).total_seconds(), 3),
            )
            atomic_write_json(manifest, manifest_path)
            archive = (
                Path(manifest_path).parent / "run_manifests" / f"{audit.run_id}.json"
            )
            atomic_write_json(manifest, archive)
            return result
        except Exception as error:
            completed = datetime.now(timezone.utc)
            manifest = audit.as_dict(
                "failed",
                completed_at=completed.isoformat(),
                duration_seconds=round((completed - started).total_seconds(), 3),
                error_type=type(error).__name__,
                error=str(error),
            )
            atomic_write_json(manifest, manifest_path)
            archive = (
                Path(manifest_path).parent / "run_manifests" / f"{audit.run_id}.json"
            )
            atomic_write_json(manifest, archive)
            raise
