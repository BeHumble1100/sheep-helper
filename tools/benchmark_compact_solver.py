"""Offline research backend. Not imported by the desktop application.

Requires Windows .NET Framework compiler. Native output is never trusted
without a complete replay in the production Python rules engine.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from sheep_helper.compact_solver import verified_result
from sheep_helper.solver import (
    Board, _iter_bits, replay, result_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/sheep_helper/native/CompactSolver.cs"


def compiler_path() -> Path:
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    for framework in ("Framework64", "Framework"):
        compiler = windows / "Microsoft.NET" / framework / "v4.0.30319/csc.exe"
        if compiler.is_file():
            return compiler
    raise RuntimeError("Experimental backend requires the Windows .NET Framework compiler")


def compile_backend() -> Path:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:16]
    folder = ROOT / "build/compact-solver" / digest
    binary = folder / "CompactSolver.exe"
    if not binary.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(compiler_path()), "/nologo", "/optimize+",
                        "/r:System.Web.Extensions.dll", f"/out:{binary}", str(SOURCE)], check=True)
    return binary


def encode(action):
    kind, value = action
    return value if kind == "board" else -1 if kind == "move_out" else -value - 2


def run_backend(board: Board, seconds: float, prefixes=(), traces=None):
    if not 0 < board.size <= 256 or len(board.type_values) > 16:
        raise ValueError("Experimental backend supports 1..256 tiles and at most 16 types")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be finite and positive")
    payload = {
        "types": [board.type_indexes[t] for t in board.types],
        "layers": list(board.layers) if board.layers else [0] * board.size,
        "blockers": [list(_iter_bits(mask)) for mask in board.blocker_masks],
        "prefixes": [[encode(a) for a in plan if a[0] != "revive_move_out"] for plan in prefixes],
    }
    if traces is not None:
        payload["traces"] = [[encode(a) for a in plan] for plan in traces]
    binary = compile_backend()
    with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
        input_path, output_path = Path(temporary) / "input.json", Path(temporary) / "output.json"
        input_path.write_text(json.dumps(payload), encoding="utf8")
        subprocess.run([str(binary), str(input_path), str(output_path), str(seconds)],
                       check=True, capture_output=True, timeout=seconds + 15)
        return json.loads(output_path.read_text(encoding="utf8"))



def read_prefix(board: Board, source: Path):
    record = json.loads(source.read_text(encoding="utf8"))
    plan = []
    for item in record["actions"]:
        kind = item["action"]
        if kind == "board":
            index = item["tile_index"]
            if (type(index) is not int or not 0 <= index < board.size
                    or item["tile_id"] != board.ids[index] or item["type"] != board.types[index]):
                raise ValueError("Prefix does not match this captured board")
            plan.append((kind, index))
        elif kind == "park" and item.get("temporary_slot") in (1, 2, 3):
            plan.append((kind, item["temporary_slot"] - 1))
        elif kind in ("move_out", "revive_move_out"):
            plan.append((kind, -1))
        else:
            raise ValueError("Invalid research prefix action")
    if replay(board, plan).moves != tuple(plan):
        raise ValueError("Prefix revival markers disagree with Python replay")
    return tuple(plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--prefix", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    board = Board.from_files(args.cards, args.coverage)
    prefixes = []
    for source in args.prefix:
        prefixes.append(read_prefix(board, source))
    raw = run_backend(board, args.seconds, prefixes=prefixes)
    result = verified_result(board, raw)
    record = result_to_dict(board, result)
    record["warm_started"] = bool(prefixes)
    record["formal_solver_changed"] = False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"solved={result.solved} best={raw['best']}/{board.size} "
          f"states={raw['states']} seconds={raw['seconds']:.3f} warm_started={bool(prefixes)}")


if __name__ == "__main__":
    main()
