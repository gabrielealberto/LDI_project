"""Launch the canonical pipeline in an isolated operating-system process."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from threading import RLock

from core.ingestion_support import atomic_write_json
from core.orchestration import LOCK_PATH, pipeline_lock_active
from core.utils import PROCESSED_DIR

from .repository import PROJECT_ROOT, RunRepository, utc_now


class RunConflict(RuntimeError):
    """Raised when another desktop or web pipeline is already active."""


class RunService:
    def __init__(self, repository: RunRepository, operation_lock=None):
        self.repository = repository
        self.operation_lock = operation_lock or RLock()

    def start(self, request_payload: dict | None = None) -> dict:
        with self.operation_lock:
            return self._start(request_payload)

    def _start(self, request_payload: dict | None = None) -> dict:
        active = self.repository.active()
        if active is not None:
            raise RunConflict(f"Run {active['run_id']} is already {active['status']}.")
        if pipeline_lock_active(LOCK_PATH):
            raise RunConflict("The desktop LDI pipeline is already running.")
        excel_path = PROCESSED_DIR / "ldi_optimization.xlsx"
        if excel_path.exists():
            try:
                with excel_path.open("r+b"):
                    pass
            except PermissionError as error:
                raise RunConflict(
                    "Close ldi_optimization.xlsx before starting a new run."
                ) from error

        run_id = str(uuid.uuid4())
        self.repository.create(run_id)
        run_dir = self.repository.run_dir(run_id)
        atomic_write_json(request_payload or {}, run_dir / "request.json")
        log_path = run_dir / "execution.log"
        environment = os.environ.copy()
        environment.update(
            {"MPLBACKEND": "Agg", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        )
        command = [
            sys.executable,
            "-m",
            "Webapp.worker",
            "--run-id",
            run_id,
            "--storage-dir",
            str(self.repository.storage_dir),
        ]
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW
        try:
            with log_path.open("ab", buffering=0) as log_stream:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )
        except Exception as error:
            self.repository.update(
                run_id,
                status="error",
                completed_at=utc_now(),
                error=f"Could not start pipeline process: {error}",
            )
            raise
        self.repository.update(run_id, pid=process.pid)
        return self.repository.get(run_id)
