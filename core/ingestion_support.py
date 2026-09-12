"""Operational primitives shared by external-data ingestion jobs."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def retry_session(
    *,
    retries: int = 4,
    backoff_factor: float = 1.0,
    timeout_statuses: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    """Create a GET session with bounded retries and exponential backoff."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff_factor,
        status_forcelist=timeout_statuses,
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def atomic_to_parquet(frame: pd.DataFrame, output_path: Path) -> None:
    """Write a Parquet dataset atomically, never exposing a partial file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, engine="pyarrow", compression="snappy", index=False)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(payload: object, output_path: Path) -> None:
    """Write a UTF-8 JSON document atomically."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
