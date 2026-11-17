"""Validated, local desktop preferences independent of game progress."""

import json
from pathlib import Path
import tempfile
import math

from .solver import DEFAULT_SOLVE_TIMEOUT


def validate_timeout(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError("求解时限请输入 1～3600 秒的数字") from None
    if isinstance(value, bool) or not math.isfinite(seconds) or not 1 <= seconds <= 3600:
        raise ValueError("求解时限请输入 1～3600 秒的数字")
    return seconds


def load_timeout(path: Path) -> float:
    try:
        record = json.loads(path.read_text(encoding="utf8"))
        if isinstance(record, dict) and record.get("version") == 1:
            return validate_timeout(record.get("solve_timeout_seconds"))
    except (OSError, ValueError):
        pass
    return DEFAULT_SOLVE_TIMEOUT


def save_timeout(path: Path, value) -> float:
    seconds = validate_timeout(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent, delete=False) as temporary:
        json.dump({"version": 1, "solve_timeout_seconds": seconds}, temporary, indent=2)
        staged = Path(temporary.name)
    try:
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
    return seconds
