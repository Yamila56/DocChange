"""Atomic evaluation artifacts, including bounded Windows sharing retries."""

import json
import tempfile
import time
from pathlib import Path


def atomic(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def atomic_text(path, value):
    """Bound Windows sharing violations; never leave a partially-written report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(value)
        for attempt in range(20):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
