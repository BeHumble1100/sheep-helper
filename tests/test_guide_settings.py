import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sheep_helper.guide_settings import load_timeout, save_timeout


class GuideSettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_corrupt_settings_fall_back_to_twenty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide-settings.json"
            self.assertEqual(load_timeout(path), 20)
            self.assertEqual(save_timeout(path, "120"), 120)
            self.assertEqual(load_timeout(path), 120)
            for raw in ('broken', '[]', '{"version":1,"solve_timeout_seconds":"nan"}'):
                path.write_text(raw, encoding="utf8")
                self.assertEqual(load_timeout(path), 20)

    def test_invalid_values_do_not_overwrite_saved_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide-settings.json"
            save_timeout(path, 60)
            for value in ("", "oops", "nan", "inf", "-inf", -1, 0, 3601, True):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    save_timeout(path, value)
                self.assertEqual(load_timeout(path), 60)

    @unittest.skipUnless(sys.platform == "win32", "Windows desktop")
    def test_ui_applies_persists_and_allows_explicit_cli_override(self):
        from sheep_helper.guide_ui import GuideApp
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            app = GuideApp(capture_dir=folder, start_capture=False)
            try:
                self.assertEqual(app.timeout, 20)
                app.timeout_text.set("120")
                app.timeout_apply_button.invoke()
                self.assertEqual(app.timeout, 120)
                app.root.update_idletasks()
                self.assertTrue(app.timeout_input.winfo_viewable())
                app.timeout_text.set("nan")
                app._begin_capture()
                self.assertEqual(app.capture_epoch, 0)
                self.assertEqual(app.timeout, 120)
            finally:
                app.close()
            app = GuideApp(capture_dir=folder, start_capture=False)
            try:
                self.assertEqual(app.timeout, 120)
                self.assertEqual(app.timeout_text.get(), "120")
            finally:
                app.close()
            app = GuideApp(capture_dir=folder, start_capture=False, timeout=60)
            try:
                self.assertEqual(app.timeout, 60)
            finally:
                app.close()

    def test_longer_budget_runs_more_twenty_second_search_groups(self):
        from sheep_helper.live import CapturedGame, PreparedGame, solve_prepared_game
        from sheep_helper.solver import SolveResult
        root = Path(__file__).resolve().parents[1] / "captured-layouts"
        map_id = "f35bb0aff5ff7fef951795a377cf0749"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            prepared = PreparedGame(
                CapturedGame(folder / "seed.json", folder / "map.map", map_id, 80002, 18),
                folder / "layout.json", root / f"{map_id}.coverage.json",
                root / f"{map_id}.cards.json", folder / "solution.json",
            )
            clock = [0.0]
            options = []
            def search(board, config):
                options.append(config)
                clock[0] += config.time_limit_seconds
                return SolveResult(False, (), config.max_states, None, config.time_limit_seconds, "budget")
            with patch("sheep_helper.live.backend_path", return_value=None), patch("sheep_helper.live.time.monotonic", side_effect=lambda: clock[0]), patch(
                    "sheep_helper.live.solve_hybrid", side_effect=search):
                with self.assertRaises(RuntimeError):
                    solve_prepared_game(prepared, timeout=60)
            self.assertEqual([c.time_limit_seconds for c in options], [20, 20, 20])
            self.assertEqual([c.seed for c in options], [0, 1, 2])
            self.assertEqual(sum(c.max_states for c in options), 10_500_000)
            self.assertFalse(prepared.solution_path.exists())
            with patch("sheep_helper.live.backend_path", return_value=None), patch("sheep_helper.live.solve_hybrid") as search:
                with self.assertRaisesRegex(RuntimeError, "求解已取消"):
                    solve_prepared_game(prepared, timeout=600, cancelled=lambda: True)
                search.assert_not_called()
