import random
import sys
import unittest
from pathlib import Path

from tools.benchmark_compact_solver import compiler_path, run_backend, verified_result
from sheep_helper.solver import Board, SearchState, transition, _iter_bits
from sheep_helper.live import load_verified_plan
from sheep_helper.solver import replay


try:
    SUPPORTED = sys.platform == "win32" and compiler_path().is_file()
except RuntimeError:
    SUPPORTED = False


@unittest.skipUnless(SUPPORTED, "Offline native experiment requires Windows .NET compiler")
class CompactExperimentTests(unittest.TestCase):
    def test_today_difficult_full_route_matches_both_rule_engines(self):
        folder = Path(__file__).parent / "fixtures/today-240"
        board = Board.from_files(folder / "cards.json", folder / "coverage.json")
        plan = load_verified_plan(board, folder / "solution.json")
        final = replay(board, plan)
        self.assertEqual(board.size, 240)
        self.assertFalse(final.remaining or final.tray or any(final.parked))
        self.assertTrue(final.move_out_used and final.revive_used)
        raw = run_backend(board, 1, traces=[tuple(a for a in plan if a[0] != "revive_move_out")])
        self.assertTrue(raw[0]["legal"])
        self.assertEqual(raw[0]["remaining"], [])
        self.assertEqual(raw[0]["tray"], [])
        self.assertEqual(raw[0]["parked"], [[], [], []])

    def test_native_transitions_match_python_on_random_paths_and_both_tools(self):
        kinds = tuple(range(7)) * 3
        masks = tuple(1 << (i - 1) if i and i % 4 == 0 else 0 for i in range(21))
        deps = tuple(tuple(i for i, mask in enumerate(masks) if mask & (1 << j)) for j in range(21))
        board = Board(tuple(map(str, range(21))), kinds, masks, deps,
                      sum(1 << i for i, mask in enumerate(masks) if not mask),
                      tuple(range(7)), {i: i for i in range(7)})
        rng = random.Random(803)
        paths, expected = [], []
        for _ in range(120):
            state = SearchState((1 << 21) - 1, board.initial_available, (), ((), (), ()), False, False, ())
            path = []
            for _ in range(28):
                choices = [("board", i) for i in _iter_bits(state.available)]
                choices += [("park", i) for i, slot in enumerate(state.parked) if slot]
                if state.tray and not state.move_out_used:
                    choices.append(("move_out", -1))
                rng.shuffle(choices)
                for action in choices:
                    child = transition(board, state, action)
                    if child is not None:
                        path.append(action)
                        state = child
                        break
                else:
                    break
                paths.append(tuple(path))
                expected.append({
                    "legal": True, "remaining": list(_iter_bits(state.remaining)),
                    "available": list(_iter_bits(state.available)), "tray": list(state.tray),
                    "parked": [list(s) for s in state.parked],
                    "move_out": state.move_out_used, "revive": state.revive_used,
                })
        actual = run_backend(board, 1, traces=paths)
        self.assertEqual(actual, expected)
        self.assertTrue(any(s["revive"] and s["move_out"] for s in expected))

    def test_native_full_solution_is_independently_verified(self):
        root = Path(__file__).resolve().parents[1] / "captured-layouts"
        board = Board.from_files(root / "f35bb0aff5ff7fef951795a377cf0749.cards.json",
                                 root / "f35bb0aff5ff7fef951795a377cf0749.coverage.json")
        raw = run_backend(board, 1)
        result = verified_result(board, raw)
        self.assertTrue(result.solved)
        raw["best"] = 0
        with self.assertRaisesRegex(ValueError, "progress"):
            verified_result(board, raw)
