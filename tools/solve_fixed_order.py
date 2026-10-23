"""Search all prop and parked-card schedules for one fixed board-pick order."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from sheep_helper.solver import (
    Board, SearchState, SolveResult, _state_key, _strip_moves,
    result_to_dict, replay, transition,
)


def solve(board: Board, order: list[int], seconds: float, max_states: int) -> SolveResult:
    if len(order) != board.size or set(order) != set(range(board.size)):
        raise ValueError("fixed order must contain every tile exactly once")
    started = time.monotonic()
    deadline = started + seconds
    initial = SearchState((1 << board.size) - 1, board.initial_available,
                          (), ((), (), ()), False, False, ())
    seen: set[tuple[object, ...]] = set()
    path: list[tuple[str, int]] = []
    best: tuple[tuple[str, int], ...] = ()
    best_board_picks = 0
    explored = 0

    def visit(state: SearchState) -> tuple[tuple[str, int], ...] | None:
        nonlocal explored, best, best_board_picks
        explored += 1
        board_picks = board.size - state.remaining.bit_count()
        if board_picks > best_board_picks:
            best_board_picks = board_picks
            best = tuple(path)
        if board_picks == board.size and not state.tray and not any(state.parked):
            return tuple(path)
        if explored % 512 == 0 and (explored >= max_states or time.monotonic() >= deadline):
            return None
        key = _state_key(board, state)
        if key in seen:
            return None
        seen.add(key)
        actions: list[tuple[str, int]] = []
        if board_picks < board.size and state.available & (1 << order[board_picks]):
            actions.append(("board", order[board_picks]))
        actions.extend(("park", slot) for slot, cards in enumerate(state.parked) if cards)
        if not state.move_out_used and state.tray:
            actions.append(("move_out", -1))

        def priority(action: tuple[str, int]) -> int:
            kind, value = action
            if kind == "move_out":
                return 100 if len(state.tray) >= 6 else 0
            card = value if kind == "board" else state.parked[value][-1]
            held = sum(board.types[item] == board.types[card] for item in state.tray)
            return (200 if held == 2 else 70 if held == 1 else 10) + (3 if kind == "board" else 0)

        actions.sort(key=priority, reverse=True)
        for action in actions:
            child = transition(board, state, action)
            if child is None:
                continue
            path.extend(child.moves)
            result = visit(_strip_moves(child))
            if result is not None:
                return result
            del path[-len(child.moves):]
            if explored >= max_states or time.monotonic() >= deadline:
                return None
        return None

    full = visit(initial)
    if full is not None:
        final = replay(board, full)
        if final.remaining or final.tray or any(final.parked):
            raise ValueError("fixed-order tool plan failed exact replay")
    return SolveResult(full is not None, full if full is not None else best,
                       explored, None, time.monotonic() - started,
                       "fixed-order tool schedule solved" if full is not None
                       else "fixed-order tool schedule exhausted or timed out")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--order", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--max-states", type=int, default=5_000_000)
    args = parser.parse_args()
    board = Board.from_files(args.cards, args.coverage)
    record = json.loads(args.order.read_text(encoding="utf-8"))
    order = record.get("candidate_order_indices") or [
        int(action["tile_index"]) for action in record["actions"] if action["action"] == "board"
    ]
    result = solve(board, order, args.seconds, args.max_states)
    args.output.write_text(json.dumps(result_to_dict(board, result), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"solved={result.solved} board_picks={sum(kind == 'board' for kind, _ in result.moves)} "
          f"states={result.explored_states} seconds={result.elapsed_seconds:.2f}", flush=True)


if __name__ == "__main__":
    main()
