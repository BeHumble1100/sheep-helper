import gc
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import subprocess

from sheep_helper.compact_solver import backend_path, solve_compact, verified_result
from sheep_helper.live import _solve_portfolio
from sheep_helper.solver import Board, DFSConfig, SolveResult, replay, solve_counts, solve_dfs, solve_hybrid


ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "captured-layouts/f35bb0aff5ff7fef951795a377cf0749.cards.json"
COVERAGE = CARDS.with_name(CARDS.name.replace("cards", "coverage"))


class SolverPortfolioTests(unittest.TestCase):
    def setUp(self):
        # Finalize earlier GUI tests' Tk cycles on the UI thread before
        # deliberately allocating from background search workers.
        gc.collect()
        self.board = Board.from_files(CARDS, COVERAGE)
        self.solution = solve_hybrid(self.board, DFSConfig(time_limit_seconds=1))
        self.assertTrue(self.solution.solved)

    def test_cancel_propagates_to_both_hybrid_phases(self):
        for search in (solve_counts, solve_dfs, solve_hybrid):
            result = search(self.board, DFSConfig(cancelled=lambda: True))
            self.assertFalse(result.solved)
            self.assertEqual(result.explored_states, 0)

    def test_compact_winner_stops_original_worker(self):
        stopped = threading.Event()

        def original(board, config):
            self.assertIsNotNone(config.cancelled)
            while not config.cancelled():
                time.sleep(0.002)
            stopped.set()
            return SolveResult(False, (), 1, None, 0, "cancelled")

        with tempfile.TemporaryDirectory() as directory, \
                patch("sheep_helper.live.backend_path", return_value=Path("test.exe")), \
                patch("sheep_helper.live.solve_hybrid", side_effect=original), \
                patch("sheep_helper.live.solve_compact", return_value=self.solution):
            result = _solve_portfolio(self.board, 1, Path(directory))
        self.assertEqual(result.moves, self.solution.moves)
        self.assertTrue(stopped.is_set())

    def test_original_winner_stops_compact_worker(self):
        stopped = threading.Event()

        def compact(board, seconds, binary, directory, cancelled):
            while not cancelled():
                time.sleep(0.002)
            stopped.set()
            return None

        with tempfile.TemporaryDirectory() as directory, \
                patch("sheep_helper.live.backend_path", return_value=Path("test.exe")), \
                patch("sheep_helper.live.solve_hybrid", return_value=self.solution), \
                patch("sheep_helper.live.solve_compact", side_effect=compact):
            result = _solve_portfolio(self.board, 1, Path(directory))
        self.assertEqual(result.moves, self.solution.moves)
        self.assertTrue(stopped.is_set())

    def test_invalid_native_solution_falls_back_to_original(self):
        invalid = SolveResult(True, (), 1, None, 0, "invalid")
        with tempfile.TemporaryDirectory() as directory, \
                patch("sheep_helper.live.backend_path", return_value=Path("test.exe")), \
                patch("sheep_helper.live.solve_hybrid", return_value=self.solution), \
                patch("sheep_helper.live.solve_compact", return_value=invalid):
            self.assertTrue(_solve_portfolio(self.board, 1, Path(directory)).solved)

    def test_backend_absence_preserves_original_and_user_cancellation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("sheep_helper.live.backend_path", return_value=None), \
                patch("sheep_helper.live.solve_compact") as compact:
            self.assertTrue(_solve_portfolio(self.board, 1, Path(directory)).solved)
            with self.assertRaisesRegex(RuntimeError, "取消"):
                _solve_portfolio(self.board, 1, Path(directory), cancelled=lambda: True)
            compact.assert_not_called()

    def test_native_verifier_rejects_forged_completion_and_bad_action_codes(self):
        for raw in (
            {"actions": [], "best": 0, "solved": True},
            {"actions": [-5], "best": 0, "solved": False},
            {"actions": [True], "best": 0, "solved": False},
        ):
            with self.assertRaises(ValueError):
                verified_result(self.board, raw)

    def test_unsupported_board_sizes_use_original_backend(self):
        self.assertIsNone(backend_path(replace(self.board, ids=())))
        self.assertIsNone(backend_path(replace(self.board, ids=tuple(map(str, range(257))))))

    @unittest.skipUnless((ROOT / "build/native/CompactSolver.exe").is_file(), "Build native helper first")
    def test_actual_native_runtime_runs_outside_source_directory_and_cleans_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            result = solve_compact(self.board, 1, backend_path(self.board), runtime)
            self.assertTrue(result.solved)
            self.assertFalse(replay(self.board, result.moves).remaining)
            self.assertEqual([p.name for p in runtime.glob("*/*")], ["CompactSolver.exe"])
            self.assertIsNone(solve_compact(self.board, 1, backend_path(self.board), runtime,
                                           cancelled=lambda: True))

    @unittest.skipUnless((ROOT / "build/native/CompactSolver.exe").is_file(), "Build native helper first")
    def test_cancelling_running_native_reaps_process_and_cleans_input(self):
        fixture = ROOT / "tests/fixtures/today-240"
        board = Board.from_files(fixture / "cards.json", fixture / "coverage.json")
        processes = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        stop = threading.Event()
        timer = threading.Timer(0.2, stop.set)
        with tempfile.TemporaryDirectory() as directory:
            timer.start()
            try:
                with patch("sheep_helper.compact_solver.subprocess.Popen", side_effect=launch):
                    self.assertIsNone(solve_compact(board, 20, backend_path(board), Path(directory), stop.is_set))
            finally:
                timer.cancel()
                timer.join()
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            self.assertEqual(list(Path(directory).rglob("input.json")), [])


if __name__ == "__main__":
    unittest.main()
