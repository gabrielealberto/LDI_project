"""Small cross-platform helpers for checking local process ownership."""

from __future__ import annotations

import os


def is_process_alive(pid: int | None) -> bool:
    """Return whether a local process identifier still refers to a process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
