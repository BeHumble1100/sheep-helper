"""Managed compact search backend, with independent Python route verification.

The distributed executable is built ahead of time. Runtime never invokes a
compiler or downloads code, and child processes run outside PyInstaller's
temporary extraction directory so closing the app can clean that directory.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

from .solver import Board, SolveResult, _iter_bits, _materialize_actions, replay


def backend_path(board: Board) -> Path | None:
    if sys.platform != "win32" or not 0 < board.size <= 256 or len(board.type_values) > 16:
        return None
    package = Path(__file__).resolve().parent
    candidate = (package / "CompactSolver.exe" if getattr(sys, "frozen", False)
                 else package.parents[1] / "build/native/CompactSolver.exe")
    return candidate if candidate.is_file() else None


def verified_result(board: Board, raw: dict) -> SolveResult:
    codes = raw["actions"]
    if not isinstance(codes, list) or any(
        type(value) is not int or not -4 <= value < board.size for value in codes
    ):
        raise ValueError("Native search returned invalid actions")
    actions = [("board", value) if value >= 0 else ("move_out", -1) if value == -1
               else ("park", -value - 2) for value in codes]
    moves = _materialize_actions(board, actions, allow_revive=True)
    final = replay(board, moves)
    if board.size - final.remaining.bit_count() != raw["best"]:
        raise ValueError("Native progress disagrees with independent Python replay")
    solved = final.remaining == 0 and not final.tray and not any(final.parked)
    if type(raw["solved"]) is not bool or raw["solved"] != solved:
        raise ValueError("Native completion disagrees with independent Python replay")
    return SolveResult(solved, moves, int(raw["states"]), None, float(raw["seconds"]),
                       "compact multi-prefix search; independently replayed")


def solve_compact(
    board: Board, seconds: float, binary: Path, runtime_dir: Path,
    cancelled: Callable[[], bool] | None = None,
) -> SolveResult | None:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be finite and positive")
    started = time.monotonic()
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    folder = runtime_dir / digest[:16]
    folder.mkdir(parents=True, exist_ok=True)
    persistent = folder / "CompactSolver.exe"
    if not persistent.is_file() or hashlib.sha256(persistent.read_bytes()).hexdigest() != digest:
        with tempfile.NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copyfile(binary, temporary)
            temporary.replace(persistent)
        finally:
            temporary.unlink(missing_ok=True)
    payload = {
        "types": [board.type_indexes[t] for t in board.types],
        "layers": list(board.layers) if board.layers else [0] * board.size,
        "blockers": [list(_iter_bits(mask)) for mask in board.blocker_masks],
    }
    with tempfile.TemporaryDirectory(dir=folder) as temporary_dir:
        input_path = Path(temporary_dir) / "input.json"
        output_path = Path(temporary_dir) / "output.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0 or (cancelled is not None and cancelled()):
            return None
        process = subprocess.Popen(
            [str(persistent), str(input_path), str(output_path), str(remaining)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            while process.poll() is None:
                if ((cancelled is not None and cancelled())
                        or time.monotonic() - started >= seconds + 0.5):
                    return None
                time.sleep(0.03)
            if process.returncode != 0:
                raise RuntimeError(f"Compact search exited with {process.returncode}")
            return verified_result(board, json.loads(output_path.read_text(encoding="utf-8-sig")))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
