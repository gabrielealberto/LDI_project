"""Scheduler-friendly entry point for one complete LDI run."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow both ``python scripts/run_ldi.py`` and ``python -m scripts.run_ldi``.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.orchestration import execute_with_manifest  # noqa: E402
from main import main  # noqa: E402


def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    execute_with_manifest(main)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
