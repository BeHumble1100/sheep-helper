"""Check a fixed-type solution against color-changing tiles in the client."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from sheep_helper.card_types import shuffled_type_pool
from sheep_helper.solver import Board, SearchState, transition


def check(cards_path: Path, coverage_path: Path, solution_path: Path) -> tuple[int | None, str]:
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    layout = json.loads(Path(str(cards_path).replace(".cards.json", ".layout.json")).read_text(encoding="utf-8"))
    solution = json.loads(solution_path.read_text(encoding="utf-8"))
    board = Board.from_files(cards_path, coverage_path)
    positions = {tile_id: index for index, tile_id in enumerate(board.ids)}
    changing = sorted(
        (tile for tile in cards["tiles"] if tile.get("meta_type") == 2),
        key=lambda tile: (tile["layer"], tile["row"], tile["col"]),
    )
    if not changing:
        return None, "地图没有变色牌；固定牌型重放适用"

    _pool, rng = shuffled_type_pool(layout, cards["map_seed"])

    types = list(board.types)
    current = board
    state = SearchState((1 << board.size) - 1, board.initial_available,
                        (), ((), (), ()), False, False, ())
    first_change: int | None = None
    for action in solution["actions"]:
        kind = action["action"]
        if kind == "revive_move_out":
            continue
        value = (action["tile_index"] if kind == "board" else
                 action["temporary_slot"] - 1 if kind == "park" else -1)
        step = action["step"]
        if kind == "board" and types[value] != action["type"] and first_change is None:
            first_change = step
        before_count = len(state.tray)
        next_state = transition(current, state, (kind, value))
        if next_state is None:
            return first_change, f"第 {step} 步在真实变色规则下槽位溢出"
        state = next_state
        if kind in ("board", "park") and len(state.tray) == before_count - 2:
            remaining = [positions[tile["id"]] for tile in changing
                         if state.remaining & (1 << positions[tile["id"]])]
            shuffled = [types[index] for index in remaining]
            rng.shuffle(shuffled)
            for index, card_type in zip(remaining, shuffled):
                types[index] = card_type
            current = replace(board, types=tuple(types))
    return first_change, "固定路线在变色规则下重放完毕"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cards", type=Path)
    args = parser.parse_args()
    cards = args.cards
    first_change, outcome = check(cards, Path(str(cards).replace(".cards.json", ".coverage.json")),
                                  Path(str(cards).replace(".cards.json", ".guide-solution.json")))
    print(f"首个牌型与固定路线不符的步骤：{first_change}")
    print(outcome)


if __name__ == "__main__":
    main()
