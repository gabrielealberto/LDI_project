"""Durable run registry and artifact access for the web application."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.processes import is_process_alive


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_DIR = PROJECT_ROOT / "data" / "webapp"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepository:
    """Persist run metadata in SQLite and large payloads as files."""

    def __init__(self, storage_dir: Path = DEFAULT_STORAGE_DIR):
        self.storage_dir = Path(storage_dir).resolve()
        self.runs_dir = self.storage_dir / "runs"
        self.database_path = self.storage_dir / "runs.sqlite3"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    pid INTEGER,
                    pipeline_run_id TEXT,
                    error TEXT
                )
                """
            )

    def run_dir(self, run_id: str) -> Path:
        candidate = (self.runs_dir / run_id).resolve()
        if candidate.parent != self.runs_dir or not run_id:
            raise ValueError("Invalid run identifier.")
        return candidate

    def create(self, run_id: str) -> dict:
        self.run_dir(run_id).mkdir(parents=True, exist_ok=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs (run_id, status, created_at) VALUES (?, ?, ?)",
                (run_id, "queued", utc_now()),
            )
        return self.get(run_id)

    def update(self, run_id: str, **fields) -> None:
        allowed = {
            "status",
            "started_at",
            "completed_at",
            "pid",
            "pipeline_run_id",
            "error",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported run fields: {sorted(invalid)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [run_id]
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?", values
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown run: {run_id}")

    def get(self, run_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def latest(self, status: str | None = None) -> dict | None:
        query = "SELECT * FROM runs"
        values = ()
        if status is not None:
            query += " WHERE status = ?"
            values = (status,)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, values).fetchone()
        return dict(row) if row else None

    def active(self) -> dict | None:
        """Return an active run, marking abandoned worker records as errors."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE status IN ('queued', 'running')
                ORDER BY created_at DESC
                """
            ).fetchall()
            for row in rows:
                record = dict(row)
                if is_process_alive(record["pid"]):
                    return record
                connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, completed_at = ?, error = ?
                    WHERE run_id = ? AND status IN ('queued', 'running')
                    """,
                    (
                        "error",
                        utc_now(),
                        "Worker process is no longer running; run was marked as abandoned.",
                        record["run_id"],
                    ),
                )
        return None

    def list(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def log(self, run_id: str) -> list[str]:
        path = self.run_dir(run_id) / "execution.log"
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()

    def artifact(self, run_id: str, name: str) -> Path:
        if Path(name).name != name or not name:
            raise ValueError("Invalid artifact name.")
        path = (self.run_dir(run_id) / "artifacts" / name).resolve()
        expected_parent = (self.run_dir(run_id) / "artifacts").resolve()
        if path.parent != expected_parent:
            raise ValueError("Invalid artifact path.")
        return path
