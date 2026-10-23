"""Compare tool-aware DFS orderings on one captured board, without game I/O."""

from __future__ import annotations

import argparse
from pathlib import Path

from sheep_helper.solver import Board, DFSConfig, replay, solve_dfs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--seconds-each", type=float, default=20.0)
    parser.add_argument("--weights", type=float, nargs="+", default=[0, 1, 2, 4])
    parser.add_argument("--branches", type=int, default=7)
    parser.add_argument("--restarts", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261021)
    args = parser.parse_args()
    board = Board.from_files(args.cards, args.coverage)
    for weight in args.weights:
        result = solve_dfs(board, DFSConfig(
            time_limit_seconds=args.seconds_each,
            max_states=10_000_000,
            max_branches=args.branches,
            restarts=args.restarts,
            seed=args.seed,
            layer_weight=weight,
        ))
        board_picks = sum(action == "board" for action, _ in result.moves)
        verified = False
        if result.solved:
            final = replay(board, result.moves)
            verified = final.remaining == 0 and not final.tray and not any(final.parked)
        print(
            f"layer_weight={weight:g} branches={args.branches} "
            f"solved={result.solved} verified={verified} "
            f"board_picks={board_picks} states={result.explored_states} "
            f"seconds={result.elapsed_seconds:.2f}",
            flush=True,
        )
        if verified:
            break


if __name__ == "__main__":
    main()
