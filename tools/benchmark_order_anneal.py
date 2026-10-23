"""Experimental full-order search for a no-tool route.

Every candidate is a topological order of all tiles. The objective penalizes
tray overflow along the entire order. Only an overflow-free order is exported
as a playable route, after exact replay in the production rule engine.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import time

from sheep_helper.solver import Board, _iter_bits, replay


def occupancy_stats(board: Board, order: list[int], target_peak: int) -> tuple[float, int, int, int]:
    counts = [0] * len(board.type_values)
    occupied = 0
    peak = 0
    overflow = 0
    first_overflow = len(order)
    burden = 0
    for index, card in enumerate(order):
        kind = board.type_indexes[board.types[card]]
        held = counts[kind]
        counts[kind] = (held + 1) % 3
        occupied += -2 if held == 2 else 1
        peak = max(peak, occupied)
        burden += occupied
        excess = max(0, occupied - 6)
        overflow += excess * excess
        if excess and first_overflow == len(order):
            first_overflow = index
    return overflow + 500.0 * max(0, peak - target_peak) + 0.03 * burden, peak, overflow, first_overflow


def extend_prefix(board: Board, prefix: list[int], rng: random.Random) -> list[int]:
    remaining = (1 << board.size) - 1
    available = board.initial_available
    counts = [0] * len(board.type_values)
    result: list[int] = []
    for card in prefix:
        if not available & (1 << card):
            raise ValueError("prefix is not a legal topological sequence")
        result.append(card)
        kind = board.type_indexes[board.types[card]]
        counts[kind] = (counts[kind] + 1) % 3
        remaining &= ~(1 << card)
        available &= ~(1 << card)
        for dependent in board.dependents[card]:
            if remaining & (1 << dependent) and not board.blocker_masks[dependent] & remaining:
                available |= 1 << dependent
    while remaining:
        options = list(_iter_bits(available))
        if not options:
            raise ValueError("coverage graph contains a cycle")
        def score(card: int) -> float:
            held = counts[board.type_indexes[board.types[card]]]
            return (100 if held == 2 else 30 if held == 1 else 0) + 2 * len(board.dependents[card]) + rng.random() * 8
        card = max(options, key=score)
        result.append(card)
        kind = board.type_indexes[board.types[card]]
        counts[kind] = (counts[kind] + 1) % 3
        remaining &= ~(1 << card)
        available &= ~(1 << card)
        for dependent in board.dependents[card]:
            if remaining & (1 << dependent) and not board.blocker_masks[dependent] & remaining:
                available |= 1 << dependent
    return result


def can_move(board: Board, card: int, origin: int, destination: int, positions: list[int]) -> bool:
    if destination > origin:
        return all(positions[dependent] > destination for dependent in board.dependents[card])
    return all(positions[blocker] < destination for blocker in _iter_bits(board.blocker_masks[card]))


def solve(board: Board, prefix: list[int], seconds: float, seed: int,
          target_peak: int, freeze_prefix: int) -> dict[str, object]:
    rng = random.Random(seed)
    started = time.monotonic()
    current = extend_prefix(board, prefix, rng)
    if not 0 <= freeze_prefix < board.size:
        raise ValueError("freeze_prefix must be less than board size")
    current_score, current_peak, current_overflow, current_first = occupancy_stats(board, current, target_peak)
    best = current.copy()
    best_score, best_peak, best_overflow, best_first = (
        current_score, current_peak, current_overflow, current_first
    )
    print(f"initial_peak={current_peak} initial_overflow={current_overflow} "
          f"first_overflow={current_first}", flush=True)
    steps = accepted = 0
    last_reported_overflow = best_overflow
    while time.monotonic() - started < seconds and best_overflow:
        steps += 1
        positions = [0] * board.size
        for index, card in enumerate(current):
            positions[card] = index
        if rng.random() < 0.8:
            center = current_first if current_first < board.size else rng.randrange(board.size)
            origin = min(board.size - 1, max(freeze_prefix, center + rng.randrange(-18, 19)))
        else:
            origin = rng.randrange(freeze_prefix, board.size)
        radius = 25 if rng.random() < 0.8 else 100
        destination = min(board.size - 1, max(freeze_prefix, origin + rng.randrange(-radius, radius + 1)))
        if origin == destination:
            continue
        card = current[origin]
        if not can_move(board, card, origin, destination, positions):
            continue
        candidate = current.copy()
        candidate.pop(origin)
        candidate.insert(destination, card)
        score, peak, overflow, first = occupancy_stats(board, candidate, target_peak)
        elapsed_fraction = min(1.0, (time.monotonic() - started) / seconds)
        temperature = 50.0 * (0.2 / 50.0) ** elapsed_fraction
        if score < current_score or rng.random() < math.exp(min(0.0, (current_score - score) / temperature)):
            current = candidate
            current_score, current_peak, current_overflow, current_first = score, peak, overflow, first
            accepted += 1
        if (peak, overflow, score) < (best_peak, best_overflow, best_score):
            best = candidate
            old_peak = best_peak
            best_score, best_peak, best_overflow, best_first = score, peak, overflow, first
            if peak < old_peak or last_reported_overflow - overflow >= 50:
                print(f"best_peak={best_peak} best_overflow={best_overflow} "
                      f"first_overflow={best_first} steps={steps} "
                      f"elapsed={time.monotonic()-started:.1f}", flush=True)
                last_reported_overflow = overflow
        if steps % 20_000 == 0 and current_overflow > best_overflow:
            current = best.copy()
            current_score, current_peak, current_overflow, current_first = (
                best_score, best_peak, best_overflow, best_first
            )
    solved = best_overflow == 0
    if solved:
        final = replay(board, (("board", card) for card in best))
        if final.remaining or final.tray:
            raise ValueError("capacity-safe topological order failed exact replay")
    return {
        "solved": solved,
        "peak_tray_without_tools": best_peak,
        "total_squared_overflow": best_overflow,
        "first_overflow_at_zero_based_pick": best_first,
        "frozen_prefix_actions": freeze_prefix,
        "attempted_moves": steps,
        "accepted_moves": accepted,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "actions": [
            {"step": index + 1, "action": "board", "tile_index": card,
             "tile_id": board.ids[card], "type": board.types[card]}
            for index, card in enumerate(best)
        ] if solved else [],
        "candidate_order_indices": [] if solved else best,
        "note": "An unsolved candidate order is diagnostic only; it overflows the tray and is not a playable route.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--target-peak", type=int, default=12)
    parser.add_argument("--freeze-prefix", type=int, default=0)
    args = parser.parse_args()
    board = Board.from_files(args.cards, args.coverage)
    prefix = []
    if args.prefix:
        record = json.loads(args.prefix.read_text(encoding="utf-8"))
        prefix = [int(action["tile_index"]) for action in record["actions"]
                  if action["action"] == "board"]
    result = solve(board, prefix, args.seconds, args.seed, args.target_peak, args.freeze_prefix)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ("actions", "candidate_order_indices")},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
