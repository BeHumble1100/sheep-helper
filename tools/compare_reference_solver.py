"""Run yangyang-hunter's own solver against the same decoded board."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--normal-seconds", type=float, default=30)
    parser.add_argument("--tool-seconds", type=float, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--actions", type=Path, help="Replay one of our plans in both rule engines")
    parser.add_argument("--output", type=Path, help="Save a verified reference solution in our action format")
    args = parser.parse_args()

    sys.path.insert(0, str(args.reference_root.resolve()))
    from yangyang_hunter.board_model import Board as ReferenceBoard, Tile
    from yangyang_hunter.solve import depth_search
    from yangyang_hunter.solve_with_tools import ToolEngine, replay_with_tools, search_with_tools
    from sheep_helper.solver import Board as OurBoard, SearchState, SolveResult, replay, result_to_dict, transition

    cards = json.loads(args.cards.read_text(encoding="utf-8"))
    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    tiles = [
        Tile(
            str(card["id"]), int(card["type"]), int(card["col"]),
            int(card["row"]), int(card["layer"]), int(card.get("mold_type", 1)),
        )
        for card in cards["tiles"]
    ]
    board = ReferenceBoard(tiles)
    by_id = {str(tile["id"]): tile for tile in coverage["tiles"]}
    for index, tile in enumerate(tiles):
        expected = sum(1 << board.index[str(blocker)] for blocker in by_id[tile.id]["blockers"])
        if board.blockers[index] != expected:
            raise ValueError(f"参考项目与本项目遮挡图不一致：{tile.id}")
    print(f"reference_geometry_matches=True tiles={len(tiles)}", flush=True)

    def save_solution(path, states, elapsed):
        if args.output is None:
            return
        moves = []
        for item in path:
            if isinstance(item, int):
                moves.append(("board", item))
            elif item == "MOVE_OUT":
                moves.append(("move_out", -1))
            elif item == "REVIVE":
                moves.append(("revive_move_out", -1))
            elif isinstance(item, tuple) and item[0] == "AUX":
                moves.append(("park", item[1]))
            else:
                raise ValueError(f"未知参考动作：{item!r}")
        ours = OurBoard.from_files(args.cards, args.coverage)
        final = replay(ours, moves)
        if final.remaining or final.tray or any(final.parked):
            raise ValueError("参考路径未通过本项目的完整回放")
        result = SolveResult(True, tuple(moves), states, args.seed, elapsed, "reference route independently replayed")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result_to_dict(ours, result), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"our_rule_replay_matches=True output={args.output}", flush=True)

    if args.actions is not None:
        ours = OurBoard.from_files(args.cards, args.coverage)
        our_state = SearchState(
            (1 << ours.size) - 1, ours.initial_available,
            (), ((), (), ()), False, False, (),
        )
        engine = ToolEngine(board)
        reference_state = engine.initial()
        actions = json.loads(args.actions.read_text(encoding="utf-8"))["actions"]
        for step in actions:
            kind = step["action"]
            if kind == "board":
                card = int(step["tile_index"])
                our_state = transition(ours, our_state, ("board", card))
                reference_state = engine.apply(reference_state, board.tiles[card].id)
            elif kind == "park":
                slot = int(step["temporary_slot"]) - 1
                our_state = transition(ours, our_state, ("park", slot))
                reference_state = engine.apply(reference_state, ("AUX", slot))
            elif kind == "move_out":
                our_state = transition(ours, our_state, ("move_out", -1))
                reference_state = engine.apply(reference_state, "MOVE_OUT")
            elif kind == "revive_move_out":
                reference_state = engine.apply(reference_state, "REVIVE")
            else:
                raise ValueError(f"未知动作：{kind}")
            if our_state is None:
                raise ValueError(f"本项目拒绝合法动作：{step['step']}")
            if len(reference_state.tray) == 7 and kind != "revive_move_out":
                continue  # Our engine applies the following REVIVE atomically.
            ours_tray = tuple(ours.types[index] for index in our_state.tray)
            ours_aux = tuple(tuple(ours.types[index] for index in slot) for slot in our_state.parked)
            ref_tray = tuple(board.tiles[index].type for index in reference_state.tray)
            ref_aux = tuple(tuple(board.tiles[index].type for index in slot) for slot in reference_state.aux)
            if (
                reference_state.removed != board.all_mask & ~our_state.remaining
                or ref_tray != ours_tray or ref_aux != ours_aux
                or reference_state.used_remove != our_state.move_out_used
                or reference_state.used_revive != our_state.revive_used
            ):
                raise ValueError(f"两套规则状态在第 {step['step']} 步分歧")
        print(f"reference_rule_replay_matches=True actions={len(actions)}", flush=True)
        return

    normal = depth_search(
        board, seconds=args.normal_seconds, seed=args.seed,
        max_branches=6, max_states=1_500_000,
    )
    prefix = normal["path"]
    print(
        f"normal_status={normal['status']} depth={len(prefix)} "
        f"states={normal.get('states')} elapsed={normal['elapsed']:.2f}",
        flush=True,
    )
    if normal["status"] == "solved":
        print(f"verified={replay_with_tools(board, prefix)}", flush=True)
        save_solution(prefix, normal.get("states", 0), normal["elapsed"])
        return
    tool = search_with_tools(
        board, seconds=args.tool_seconds, max_states=2_000_000,
        max_branches=8, seed=args.seed, prefix=prefix,
    )
    print(
        f"tool_status={tool['status']} depth={tool['depth']} "
        f"states={tool['states']} elapsed={tool['elapsed']:.2f} "
        f"verified={replay_with_tools(board, tool['path']) if tool['status'] == 'solved' else False}",
        flush=True,
    )
    if tool["status"] == "solved" and replay_with_tools(board, tool["path"]):
        save_solution(tool["path"], tool["states"], tool["elapsed"])


if __name__ == "__main__":
    main()
