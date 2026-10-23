"""Experimental limited-discrepancy search for a no-tool tile route.

Unlike ordinary DFS, this explores all paths with at most k non-greedy moves
before raising k. It is a diagnostic algorithm; it does not claim that a
partial route is winnable with tools.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from sheep_helper.solver import Board, SolveResult, _iter_bits, replay, result_to_dict


def solve(board: Board, seconds: float, max_states: int, branches: int) -> SolveResult:
    started = time.monotonic()
    deadline = started + seconds
    all_remaining = (1 << board.size) - 1
    remaining_counts = [0] * len(board.type_values)
    for card_type in board.types:
        remaining_counts[board.type_indexes[card_type]] += 1
    tray_counts = [0] * len(board.type_values)
    path: list[int] = []
    best: tuple[int, ...] = ()
    explored = 0
    solved = False

    def remove(remaining: int, available: int, card: int) -> tuple[int, int]:
        remaining &= ~(1 << card)
        available &= ~(1 << card)
        for dependent in board.dependents[card]:
            bit = 1 << dependent
            if remaining & bit and not board.blocker_masks[dependent] & remaining:
                available |= bit
        return remaining, available

    for discrepancy_limit in range(board.size + 1):
        if time.monotonic() >= deadline or explored >= max_states:
            break
        seen: dict[int, int] = {}
        print(f"discrepancy_limit={discrepancy_limit} best={len(best)} "
              f"states={explored} elapsed={time.monotonic()-started:.1f}", flush=True)

        def visit(remaining: int, available: int, occupied: int, discrepancy: int) -> bool:
            nonlocal explored, best, solved
            explored += 1
            if explored % 512 == 0 and (explored >= max_states or time.monotonic() >= deadline):
                return False
            if len(path) > len(best):
                best = tuple(path)
            if remaining == 0:
                solved = occupied == 0
                return solved
            old_discrepancy = seen.get(remaining)
            if old_discrepancy is not None and old_discrepancy <= discrepancy:
                return False
            seen[remaining] = discrepancy
            candidates = []
            for card in _iter_bits(available):
                kind = board.type_indexes[board.types[card]]
                held = tray_counts[kind]
                if occupied == 6 and held != 2:
                    continue
                score = (100 if held == 2 else 50 if held else 0)
                score += (30 if remaining_counts[kind] >= 3 else
                          20 if remaining_counts[kind] == 2 else 0)
                if board.layers:
                    score += 2 * board.layers[card]
                if occupied >= 5 and held:
                    score += 40
                candidates.append((score, card, kind))
            candidates.sort(key=lambda item: item[0], reverse=True)
            for rank, (_score, card, kind) in enumerate(candidates[:branches]):
                next_discrepancy = discrepancy + (rank > 0)
                if next_discrepancy > discrepancy_limit:
                    continue
                held = tray_counts[kind]
                tray_counts[kind] = (held + 1) % 3
                remaining_counts[kind] -= 1
                path.append(card)
                next_remaining, next_available = remove(remaining, available, card)
                next_occupied = occupied - 2 if held == 2 else occupied + 1
                if visit(next_remaining, next_available, next_occupied, next_discrepancy):
                    return True
                path.pop()
                tray_counts[kind] = held
                remaining_counts[kind] += 1
                if explored >= max_states or time.monotonic() >= deadline:
                    return False
            return False

        if visit(all_remaining, board.initial_available, 0, 0):
            break
    moves = tuple(("board", card) for card in (path if solved else best))
    if solved:
        final = replay(board, moves)
        if final.remaining or final.tray:
            raise ValueError("LDS route failed full replay")
    return SolveResult(solved, moves, explored, None, time.monotonic() - started,
                       "limited-discrepancy solved" if solved else "limited-discrepancy budget exhausted")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--max-states", type=int, default=20_000_000)
    parser.add_argument("--branches", type=int, default=6)
    args = parser.parse_args()
    board = Board.from_files(args.cards, args.coverage)
    result = solve(board, args.seconds, args.max_states, args.branches)
    args.output.write_text(json.dumps(result_to_dict(board, result), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"solved={result.solved} board_picks={len(result.moves)} "
          f"states={result.explored_states} seconds={result.elapsed_seconds:.2f}", flush=True)


if __name__ == "__main__":
    main()
