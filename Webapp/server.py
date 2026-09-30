"""Production-oriented LAN server entry point with optional Chrome launch."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from waitress import serve

from .app import create_app


DEFAULT_HOST = "192.168.3.164"
DEFAULT_PORT = 5000


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off"}


def _chrome_path() -> Path | None:
    executable = shutil.which("chrome") or shutil.which("chrome.exe")
    if executable:
        return Path(executable)
    candidates = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(
                Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )
    return next((path for path in candidates if path.is_file()), None)


def _open_chrome(url: str) -> None:
    chrome = _chrome_path()
    if chrome is not None:
        subprocess.Popen([str(chrome), "--new-window", url])
        return
    webbrowser.open(url, new=1)


def _wait_until_ready_and_open(host: str, port: int, url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
            _open_chrome(url)
            return
        except OSError:
            time.sleep(0.2)


def run() -> None:
    host = os.environ.get("LDI_WEB_HOST", DEFAULT_HOST)
    port = int(os.environ.get("LDI_WEB_PORT", str(DEFAULT_PORT)))
    browser_host = os.environ.get("LDI_WEB_BROWSER_HOST", host)
    if browser_host in {"0.0.0.0", "::"}:
        browser_host = "127.0.0.1"
    url = f"http://{browser_host}:{port}/"
    if _env_flag("LDI_WEB_OPEN_BROWSER", True):
        threading.Thread(
            target=_wait_until_ready_and_open,
            args=(browser_host, port, url),
            daemon=True,
            name="ldi-browser-launcher",
        ).start()
    print(f"LDI web app available at {url}", flush=True)
    serve(create_app(), host=host, port=port, threads=8)


if __name__ == "__main__":
    run()
