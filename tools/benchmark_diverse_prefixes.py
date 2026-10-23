"""Test diverse no-tool prefixes before tool-aware continuation.

This is an experiment, not a replacement for the verified hybrid solver.
It samples legal states during count-state DFS, then spends an equal suffix
budget on structurally different prefixes instead of only the deepest one.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import time

from sheep_helper.solver import Board, DFSConfig, SolveResult, replay, result_to_dict, solve_counts, solve_dfs


@dataclass(frozen=True)
class Prefix:
    path: tuple[tuple[str, int], ...]
    remaining: int
    occupied: int

    @property
    def depth(self) -> int:
        return len(self.path)


def choose_prefixes(
    samples: list[Prefix], count: int, min_distance: int, tray_penalty: float,
) -> list[Prefix]:
    """Greedily pick deep paths with different removed-tile sets."""
    selected: list[Prefix] = []
    for sample in sorted(samples, key=lambda item: item.depth - tray_penalty * item.occupied, reverse=True):
        if all((sample.remaining ^ prior.remaining).bit_count() >= min_distance for prior in selected):
            selected.append(sample)
            if len(selected) == count:
                break
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prefix-states", type=int, default=1_500_000)
    parser.add_argument("--prefix-seconds", type=float, default=30)
    parser.add_argument("--prefix-branches", type=int, default=6)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--candidates", type=int, default=8)
    parser.add_argument("--min-depth", type=int, default=120)
    parser.add_argument("--min-distance", type=int, default=16)
    parser.add_argument("--tray-penalty", type=float, default=0.25)
    parser.add_argument("--tail-states", type=int, default=250_000)
    parser.add_argument("--tail-seconds", type=float, default=15)
    parser.add_argument("--tail-branches", type=int, default=8)
    parser.add_argument("--move-out-min-tray", type=int, default=1)
    args = parser.parse_args()

    board = Board.from_files(args.cards, args.coverage)
    started = time.monotonic()
    # Keep a bounded set in each depth band, so deep near-duplicates do not
    # crowd out earlier paths that leave more freedom for the tools.
    bands: dict[int, list[Prefix]] = {}

    def observe(path, remaining, occupied):
        depth = len(path)
        if depth < args.min_depth:
            return
        band = (depth - args.min_depth) // 15
        group = bands.setdefault(band, [])
        candidate = Prefix(path, remaining, occupied)
        if len(group) < 300:
            group.append(candidate)
        else:
            worst = min(range(len(group)), key=lambda index: group[index].depth - args.tray_penalty * group[index].occupied)
            if candidate.depth - args.tray_penalty * candidate.occupied > group[worst].depth - args.tray_penalty * group[worst].occupied:
                group[worst] = candidate

    normal = solve_counts(board, DFSConfig(
        time_limit_seconds=args.prefix_seconds,
        max_states=args.prefix_states,
        max_branches=args.prefix_branches,
        restarts=1,
        seed=args.seed,
    ), prefix_observer=observe)
    if normal.solved:
        args.output.write_text(json.dumps(result_to_dict(board, normal), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"no_tool_solved=True states={normal.explored_states}", flush=True)
        return
    all_samples = [sample for group in bands.values() for sample in group]
    deepest_state = replay(board, normal.moves)
    all_samples.append(Prefix(normal.moves, deepest_state.remaining, len(deepest_state.tray)))
    prefixes = choose_prefixes(all_samples, args.candidates, args.min_distance, args.tray_penalty)
    print(f"normal_best={len(normal.moves)} samples={len(all_samples)} selected={len(prefixes)}", flush=True)
    best = normal.moves
    explored = normal.explored_states
    diagnostics = []
    for position, prefix in enumerate(prefixes, start=1):
        initial = replay(board, prefix.path)
        if initial.remaining != prefix.remaining:
            raise ValueError("Sampled prefix failed replay")
        tail = solve_dfs(board, DFSConfig(
            time_limit_seconds=args.tail_seconds,
            max_states=args.tail_states,
            max_branches=args.tail_branches,
            restarts=1,
            seed=args.seed,
            move_out_min_tray=args.move_out_min_tray,
        ), initial_state=initial)
        explored += tail.explored_states
        full = prefix.path + tail.moves
        board_picks = sum(kind == "board" for kind, _ in full)
        diagnostics.append({
            "prefix_depth": prefix.depth,
            "prefix_tray": prefix.occupied,
            "prefix_distance_to_deepest": (prefix.remaining ^ deepest_state.remaining).bit_count(),
            "tail_states": tail.explored_states,
            "tail_seconds": round(tail.elapsed_seconds, 3),
            "best_board_picks": board_picks,
            "solved": tail.solved,
        })
        print(f"candidate={position} prefix={prefix.depth} tray={prefix.occupied} "
              f"distance={diagnostics[-1]['prefix_distance_to_deepest']} "
              f"best={board_picks} states={tail.explored_states} solved={tail.solved}", flush=True)
        if board_picks > sum(kind == "board" for kind, _ in best):
            best = full
        if tail.solved:
            final = replay(board, full)
            if final.remaining or final.tray or any(final.parked):
                raise ValueError("Combined route failed complete replay")
            result = SolveResult(True, full, explored, position - 1, time.monotonic() - started,
                                 "diverse-prefix tool search solved")
            break
    else:
        result = SolveResult(False, best, explored, None, time.monotonic() - started,
                             "diverse-prefix search budget exhausted")
    record = result_to_dict(board, result)
    record["diagnostics"] = diagnostics
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"solved={result.solved} best_board_picks={sum(kind == 'board' for kind, _ in result.moves)} "
          f"states={explored} seconds={result.elapsed_seconds:.2f}", flush=True)


if __name__ == "__main__":
    main()
