"""Safe JSON persistence helpers."""
from __future__ import annotations

import json
import os
from typing import Any


def atomic_write_json(path: str, value: Any) -> None:
    """Write JSON atomically so an interrupted process cannot corrupt it."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise