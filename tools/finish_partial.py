"""Try an exact, wide continuation of a verified partial route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sheep_helper.solver import (
    Board, DFSConfig, SolveResult, replay, result_to_dict, solve_dfs,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--partial", type=Path, required=True)
    parser.add_argument("--prefix-actions", type=int, help="Only replay the first N input actions")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--max-states", type=int, default=10_000_000)
    parser.add_argument("--branches", type=int, default=100)
    parser.add_argument("--move-out-min-tray", type=int, default=5)
    args = parser.parse_args()

    board = Board.from_files(args.cards, args.coverage)
    record = json.loads(args.partial.read_text(encoding="utf-8"))
    if record.get("solved"):
        raise ValueError("输入已经是完整路线")
    prefix = []
    for item in record["actions"]:
        kind = item["action"]
        value = int(item["tile_index"]) if kind == "board" else (
            int(item["temporary_slot"]) - 1 if kind == "park" else -1
        )
        prefix.append((kind, value))
    if args.prefix_actions is not None:
        if not 0 <= args.prefix_actions <= len(prefix):
            raise ValueError("--prefix-actions 超出了输入路线长度")
        prefix = prefix[:args.prefix_actions]
    initial = replay(board, prefix)
    print(
        f"verified_prefix_board_picks={board.size - initial.remaining.bit_count()} "
        f"remaining={initial.remaining.bit_count()} tray={len(initial.tray)} "
        f"parked={sum(map(len, initial.parked))}",
        flush=True,
    )
    tail = solve_dfs(board, DFSConfig(
        time_limit_seconds=args.seconds,
        max_states=args.max_states,
        max_branches=args.branches,
        restarts=1,
        layer_weight=2,
        move_out_min_tray=args.move_out_min_tray,
    ), initial_state=initial)
    full = tuple(prefix) + tail.moves
    exhaustive = (
        not tail.solved
        and args.move_out_min_tray == 1
        and args.branches >= initial.remaining.bit_count() + 4
        and tail.explored_states < args.max_states
        and tail.elapsed_seconds < args.seconds
    )
    output = SolveResult(
        tail.solved, full, tail.explored_states, tail.restart,
        tail.elapsed_seconds,
        "fixed prefix has no completion in the modeled game" if exhaustive
        else f"tail search: {tail.reason}",
    )
    if output.solved:
        final = replay(board, full)
        if final.remaining or final.tray or any(final.parked):
            raise ValueError("补全路线没有通过完整回放")
    args.output.write_text(json.dumps(result_to_dict(board, output), ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"solved={output.solved} board_picks={sum(action == 'board' for action, _ in full)} "
        f"states={tail.explored_states} seconds={tail.elapsed_seconds:.2f} "
        f"exhaustive={exhaustive}",
        flush=True,
    )


if __name__ == "__main__":
    main()
