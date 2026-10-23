from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from sheep_helper.live import CapturedGame, GuidedPlay, PreparedGame, fetch_missing_map, find_captured_game, load_verified_plan, solve_prepared_game
from sheep_helper.guide_ui import GuideApp, _owned_capture_pid, _start_capture, target_level_label, tile_center
from sheep_helper.vision import Alignment
from sheep_helper.card_faces import FACE_NAMES, board_region, face_name
from sheep_helper.game_capture_addon import CAPTURE_DONE_MARKER
from sheep_helper.solver import Board, SolveResult


ROOT = Path(__file__).resolve().parents[1]
MAP_ID = "f35bb0aff5ff7fef951795a377cf0749"
CARDS = ROOT / "captured-layouts" / f"{MAP_ID}.cards.json"
COVERAGE = ROOT / "captured-layouts" / f"{MAP_ID}.coverage.json"
SOLUTION = ROOT / "captured-layouts" / f"{MAP_ID}.solution.json"


class LiveGuideTests(unittest.TestCase):
    def test_missing_public_map_is_fetched_from_fixed_asset_host(self) -> None:
        daily_map_id = "b729dfc98d8c6e516dddff3f68ae1381"
        raw = (ROOT / "captured-layouts" / "maps" / f"{daily_map_id}.map").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            seed_dir = folder / "seeds"
            seed_dir.mkdir()
            (seed_dir / "recovered-1.json").write_text(json.dumps({
                "map_md5": ["046ef1bab26e5b9bfe2473ded237b572", daily_map_id],
                "map_seed": [1, 2, 3, 4],
            }), encoding="utf-8")
            response = Mock(status=200)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw
            with patch("sheep_helper.live.urlopen", return_value=response) as open_url:
                destination = fetch_missing_map(folder, not_before=0)
            open_url.assert_called_once_with(
                f"https://cat-match-static.easygame2021.com/maps/{daily_map_id}.map", timeout=8.0)
            self.assertEqual(destination.read_bytes(), raw)
            self.assertIsNone(fetch_missing_map(folder, not_before=0))

    def test_missing_map_rejects_invalid_id_without_network_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            seed_dir = folder / "seeds"
            seed_dir.mkdir()
            (seed_dir / "recovered-1.json").write_text(json.dumps({
                "map_md5": ["046ef1bab26e5b9bfe2473ded237b572", "../../other"],
                "map_seed": [1, 2, 3, 4],
            }), encoding="utf-8")
            with patch("sheep_helper.live.urlopen") as open_url:
                self.assertIsNone(fetch_missing_map(folder, not_before=0))
            open_url.assert_not_called()

    def test_daily_level_is_labeled_from_map_key_even_on_generic_endpoint(self) -> None:
        self.assertEqual(target_level_label(900221, "/sheep/v1/game/map_info_ex"), "每日关卡")
        self.assertEqual(target_level_label(100022, "/sheep/v1/game/world/game_start"), "第二关")

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_latest_capture_button_requires_recent_complete_game(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                with patch("sheep_helper.guide_ui.find_captured_game", return_value=None):
                    app._load_latest_capture()
                self.assertIn("最近一小时没有", app.status.get())
                self.assertFalse(app.latest)
                with patch("sheep_helper.guide_ui.find_captured_game", return_value=Mock()), patch.object(
                        app, "_begin_capture") as begin:
                    app._load_latest_capture()
                    begin.assert_called_once_with()
                self.assertTrue(app.latest)
                self.assertTrue(app.resume_requested)
                app.latest = False
                with patch.object(app, "_capture_worker"):
                    app._begin_capture()
                self.assertFalse(app.resume_requested)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_capture_worker_routes_only_fresh_capture_and_restores_on_finish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=True)
            try:
                with patch("sheep_helper.guide_ui._start_capture", return_value=None), patch.object(
                        app.capture_proxy, "activate") as activate:
                    app._capture_worker(True)
                    self.assertEqual(app.events.get_nowait(), ("capture_ready", (None, True), 0))
                    activate.assert_called_once_with()
                    app._capture_worker(False)
                    self.assertEqual(app.events.get_nowait(), ("capture_ready", (None, False), 0))
                    activate.assert_called_once_with()
                with patch.object(app.capture_proxy, "restore") as restore:
                    self.assertIsNone(app._finish_capture_recording())
                    restore.assert_called_once_with()
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_uncalibrated_daily_plan_does_not_draw_or_offer_a_click(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792637807477" / "verified"
        cards = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards, next(folder.glob("*.coverage.json")))
        plan = load_verified_plan(board, next(folder.glob("*.guide-solution.json")))
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, cards, Path(directory) / "progress.json",
                               target_label="每日关卡")
                app._draw_overlay(565, 1006)
                self.assertFalse(app.canvas.find_all())
                self.assertEqual(app.confirm_button["state"], "disabled")
                self.assertFalse(app.face_verified)
                self.assertIn("尚未实测核对", app.step_text.get())
                app.last_rect = (1, 0, 0, 565, 1006)
                app._toggle_calibration()
                self.assertFalse(app.calibrated)
                self.assertFalse((Path(directory) / "guide-calibration-daily.json").exists())
                app.calibration_attempted = True
                app.events.put(("alignment", Alignment(282, 490, 52, 1.2)))
                with patch("sheep_helper.guide_ui.game_client_rect", return_value=app.last_rect):
                    app._tick()
                self.assertEqual(app.center_x.get(), 282)
                self.assertFalse(app.calibrated)
                self.assertEqual(app.confirm_button["state"], "disabled")
                self.assertIn("点击标记继续隐藏", app.status.get())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_world_alignment_failure_keeps_preview_but_disables_clicks(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json")
                app.last_rect = (1, 0, 0, 565, 1006)
                app._apply_saved_calibration(565, 1006)
                self.assertAlmostEqual(app.cell_px.get(), 565 / 11)
                self.assertAlmostEqual(app.center_x.get(), 565 / 2)
                app._draw_overlay(565, 1006)
                self.assertIn("line", {app.canvas.type(item) for item in app.canvas.find_all()})
                self.assertFalse(app.calibrated)
                self.assertEqual(app.confirm_button["state"], "disabled")
                self.assertEqual(app.auto_click_button["state"], "disabled")
                self.assertIn("待校准", app.step_text.get())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_manual_world_calibration_enables_opt_in_auto_click(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json")
                app.last_rect = (1, 0, 0, 565, 1006)
                app.center_x.set(282)
                app.center_y.set(435)
                app.cell_px.set(52)
                app._toggle_calibration()
                self.assertTrue(app.calibrated)
                self.assertTrue(app.auto_verified)
                self.assertEqual(app.auto_click_button["state"], "normal")
                self.assertFalse(app.auto_click_enabled)
                app._toggle_auto_click()
                self.assertTrue(app.auto_click_enabled)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_world_uses_exposed_card_alignment_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                expected = Alignment(282, 435, 52, 0.8)
                with patch("sheep_helper.guide_ui.find_alignment", return_value=None), patch(
                        "sheep_helper.guide_ui.find_daily_alignment", return_value=expected), patch(
                        "sheep_helper.guide_ui.is_exposed_card", return_value=True):
                    app._alignment_worker(Image.new("RGB", (565, 1006)), [(20, 20)] * 8,
                                          28, 30, False, (20, 20))
                self.assertEqual(app.events.get_nowait()[1], expected)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_world_rejects_large_pitch_and_retries_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                expected = Alignment(282, 465, 51, 0.8)
                with patch("sheep_helper.guide_ui.find_alignment",
                           return_value=Alignment(304, 495, 63, 2.0)), patch(
                        "sheep_helper.guide_ui.find_daily_alignment", return_value=expected) as fallback, patch(
                        "sheep_helper.guide_ui.is_exposed_card", return_value=True):
                    app._alignment_worker(Image.new("RGB", (565, 1006)), [(20, 20)] * 8,
                                          28, 30, False, (20, 20))
                fallback.assert_called_once()
                self.assertEqual(app.events.get_nowait()[1], expected)
                app.last_rect = (1, 0, 0, 565, 1006)
                self.assertFalse(app._alignment_in_bounds(Alignment(304, 495, 63, 2.0)))
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_world_does_not_reuse_oversized_saved_pitch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            (capture_dir / "guide-calibration.json").write_text(json.dumps({
                "version": 2, "stage": "second", "center_x_ratio": 0.54,
                "center_y_ratio": 0.462, "cell_ratio": 0.1112,
            }), encoding="utf-8")
            app = GuideApp(capture_dir=capture_dir, start_capture=False)
            try:
                self.assertIsNone(app.saved_calibration)
                app._apply_saved_calibration(565, 1006)
                self.assertAlmostEqual(app.cell_px.get(), 565 / 11)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_offline_daily_route_cannot_bypass_face_check(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792645497862" / "verified"
        cards = next(folder.glob("*.cards.json"))
        coverage = next(folder.glob("*.coverage.json"))
        solution = next(folder.glob("*.guide-solution.json"))
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), cards=cards, coverage=coverage,
                           solution=solution, start_capture=False)
            try:
                self.assertEqual(app.target_label, "每日关卡")
                self.assertFalse(app.face_verified)
                self.assertEqual(app.confirm_button["state"], "disabled")
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_daily_face_check_accepts_visible_tiles_from_different_rows(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792645497862" / "verified"
        source = next(folder.glob("*.cards.json"))
        board = Board.from_files(source, next(folder.glob("*.coverage.json")))
        plan = load_verified_plan(board, next(folder.glob("*.guide-solution.json")))
        available = [board.ids[index] for index in range(board.size)
                     if board.initial_available & (1 << index)]
        observed = [available[index] for index in (0, len(available) // 3,
                                                   2 * len(available) // 3, len(available) - 1)]
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            record = json.loads(source.read_text(encoding="utf-8"))
            record["verification_mode"] = "exact-type"
            record["verified_visible_ids"] = observed
            cards = capture_dir / "observed.cards.json"
            cards.write_text(json.dumps(record), encoding="utf-8")
            app = GuideApp(capture_dir=capture_dir, start_capture=False)
            try:
                app._set_guide(board, plan, cards, capture_dir / "progress.json",
                               target_label="每日关卡")
                self.assertTrue(app.face_verified)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_daily_waits_for_manual_first_level_even_if_faces_are_unverified(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792637807477" / "verified"
        cards = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards, next(folder.glob("*.coverage.json")))
        plan = load_verified_plan(board, next(folder.glob("*.guide-solution.json")))
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, cards, Path(directory) / "progress.json",
                               wait_for_second=True, target_label="每日关卡")
                self.assertTrue(app.waiting_for_second)
                self.assertFalse(app.face_verified)
                self.assertEqual(app.confirm_button["state"], "normal")
                self.assertEqual(app.confirm_button["text"], "我已进入每日第二关")
                self.assertEqual(app.auto_button["state"], "disabled")
                app.last_rect = (1, 0, 0, 565, 1006)
                with patch.object(app, "_request_auto_calibration") as align:
                    app._confirm()
                    align.assert_called_once()
                self.assertFalse(app.waiting_for_second)
                self.assertEqual(app.confirm_button["state"], "disabled")
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_corrupt_calibration_is_ignored_and_daily_uses_separate_file(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            (capture_dir / "guide-calibration.json").write_text(json.dumps({
                "stage": "second", "center_x_ratio": 0.012,
                "center_y_ratio": 0.001, "cell_ratio": 0.002,
            }), encoding="utf-8")
            app = GuideApp(capture_dir=capture_dir, start_capture=False)
            try:
                self.assertEqual(app.timeout, 20.0)
                self.assertIsNone(app.saved_calibration)
                app._set_guide(board, plan, CARDS, capture_dir / "progress.json",
                               wait_for_second=True, target_label="每日关卡")
                self.assertEqual(app.calibration_file.name, "guide-calibration-daily.json")
                self.assertIsNone(app.saved_calibration)
                (capture_dir / "guide-calibration-daily.json").write_text(json.dumps({
                    "stage": "daily", "center_x_ratio": 0.5,
                    "center_y_ratio": 0.43, "cell_ratio": 1 / 9,
                }), encoding="utf-8")
                self.assertIsNone(app._read_calibration())
                app.last_rect = (1, 0, 0, 565, 1006)
                app._apply_saved_calibration(565, 1006)
                self.assertGreater(app.cell_px.get(), 40)
                self.assertLess(app.cell_px.get(), 55)
                self.assertFalse(app._alignment_in_bounds(Alignment(7, 1, 1, 2.0)))
                self.assertTrue(app._alignment_in_bounds(Alignment(282, 490, 52, 2.0)))
            finally:
                app.close()

    def test_listener_reuse_only_targets_the_same_capture_directory(self) -> None:
        folder = Path(r"D:\develop\sheep_helper\captured-layouts")
        rows = [
            {"ProcessId": 41, "CommandLine":
             f"mitmdump.exe --listen-port 18888 -s game_capture_addon.py "
             f"--set game_capture_dir={folder} -s seed_recovery_addon.py"},
            {"ProcessId": 42, "CommandLine":
             "mitmdump.exe --listen-port 18888 -s other.py --set game_capture_dir=C:\\other"},
        ]
        result = Mock(stdout=json.dumps(rows))
        with patch("sheep_helper.guide_ui.subprocess.run", return_value=result):
            self.assertEqual(_owned_capture_pid(folder, 18888), 41)
            self.assertIsNone(_owned_capture_pid(folder, 18889))

    def test_existing_owned_listener_is_reused_without_network_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("sheep_helper.guide_ui._port_running", return_value=True), patch(
                    "sheep_helper.guide_ui._owned_capture_pid", return_value=41), patch(
                    "sheep_helper.guide_ui.subprocess.Popen") as launch:
                self.assertIsNone(_start_capture(Path(directory)))
                launch.assert_not_called()

    def test_stale_owned_listener_is_restarted_before_a_new_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "game-capture-status.json").write_text(
                json.dumps({"capture_format_version": 1, "listener_pid": 41}), encoding="utf-8"
            )
            with patch("sheep_helper.guide_ui._port_running", side_effect=[True, False]), patch(
                    "sheep_helper.guide_ui._owned_capture_pid", return_value=41), patch(
                    "sheep_helper.guide_ui.os.kill") as stop, patch(
                    "sheep_helper.guide_ui.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "mitmdump"):
                    _start_capture(Path(directory))
                stop.assert_called_once()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_unsolved_stops_recording_but_keeps_proxy_and_rearms_button(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json",
                               wait_for_second=True)
                process = Mock()
                process.poll.return_value = None
                app.capture_process = process
                app.latest = True
                app.capture_button.configure(text="抓包中", state="disabled")
                app.events.put(("unsolved", "20 秒内未找到完整路线"))
                with patch("sheep_helper.guide_ui.messagebox.showinfo"), patch(
                        "sheep_helper.guide_ui.game_client_rect", return_value=None):
                    app._tick()
                process.terminate.assert_not_called()
                self.assertIs(app.capture_process, process)
                self.assertTrue((Path(directory) / CAPTURE_DONE_MARKER).exists())
                self.assertIsNone(app.guide)
                self.assertFalse(app.waiting_for_second)
                self.assertFalse(app.latest)
                self.assertEqual(app.capture_button["state"], "normal")
                self.assertEqual(app.capture_button["text"], "开始抓包")
                self.assertIn("先点击「开始抓包」", app.status.get())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_unsolved_rearms_capture_and_restores_system_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                with patch.object(app.capture_proxy, "restore", return_value=True):
                    app._reset_capture_cycle("没有完整路线")
                self.assertIn("原系统代理已恢复", app.status.get())
                self.assertEqual(app.capture_button["state"], "normal")
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_pause_resets_capture_even_before_a_guide_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app.capture_epoch = 4
                app.capture_button.configure(text="抓包中", state="disabled")
                app.latest_button.configure(state="disabled")
                app.step_text.set("等待目标关卡完整路线")
                app.events.put(("capture_ready", (None, True), 4))
                with patch.object(app.capture_proxy, "restore", return_value=True) as restore:
                    app._pause()
                    restore.assert_called_once_with()
                self.assertEqual(app.capture_epoch, 5)
                self.assertTrue((Path(directory) / CAPTURE_DONE_MARKER).exists())
                self.assertIsNone(app.guide)
                self.assertEqual(app.capture_button["text"], "开始抓包")
                self.assertEqual(app.capture_button["state"], "normal")
                self.assertEqual(app.latest_button["state"], "normal")
                self.assertEqual(app.auto_click_button["state"], "disabled")
                with patch("sheep_helper.guide_ui.game_client_rect", return_value=None):
                    app._tick()
                self.assertEqual(app.capture_button["text"], "开始抓包")
                self.assertIn("已暂停并重置本局", app.status.get())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_pause_discards_pending_solution_and_guide(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json",
                               wait_for_second=True)
                old_epoch = app.capture_epoch
                app.events.put(("ready", (board, plan, CARDS,
                                           Path(directory) / "progress.json", True), old_epoch))
                with patch.object(app.capture_proxy, "restore", return_value=False), patch(
                        "sheep_helper.guide_ui.game_client_rect", return_value=None):
                    app._pause()
                    app._tick()
                self.assertIsNone(app.guide)
                self.assertFalse(app.waiting_for_second)
                self.assertFalse(app.calibrated)
                self.assertFalse(app.auto_click_enabled)
                self.assertEqual(app.confirm_button["state"], "disabled")
                self.assertEqual(app.step_text.get(), "等待新局 · 请再次点击「开始抓包」")
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_paused_capture_worker_does_not_activate_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app.capture_epoch = 7
                with patch("sheep_helper.guide_ui._start_capture", side_effect=lambda _dir: app._pause()), patch.object(
                        app.capture_proxy, "activate") as activate, patch.object(
                        app.capture_proxy, "restore", return_value=False):
                    app._capture_worker(True, 7)
                activate.assert_not_called()
                self.assertEqual(app.capture_epoch, 8)
                self.assertTrue(app.events.empty())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_closing_helper_keeps_proxy_for_wechat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            process = Mock()
            process.poll.return_value = None
            app.capture_process = process
            app.close()
            process.terminate.assert_not_called()
            self.assertTrue((Path(directory) / CAPTURE_DONE_MARKER).exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_second_level_route_offers_visible_manual_fallback(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json",
                               wait_for_second=True)
                self.assertTrue(app.waiting_for_second)
                self.assertFalse(app.calibrated)
                self.assertIn("等待你确认进入第二关", app.step_text.get())
                self.assertEqual(app.confirm_button["state"], "normal")
                self.assertEqual(app.confirm_button["text"], "我已进入第二关")
                self.assertIn("立即检查第二关", app.auto_button["text"])
                with patch.object(app, "_request_auto_calibration") as check:
                    app.last_rect = (1, 0, 0, 565, 1071)
                    app._confirm()
                    check.assert_called_once_with()
                app.waiting_for_second = False
                app.events.put(("alignment", Alignment(282, 500, 52, 2.0)))
                with patch("sheep_helper.guide_ui.game_client_rect", return_value=app.last_rect):
                    app._tick()
                self.assertFalse(app.waiting_for_second)
                self.assertTrue(app.calibrated)
                self.assertEqual(app.confirm_button["text"], "我已点此牌")
                self.assertIn("第 1 步", app.step_text.get())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_success_stops_recording_but_keeps_proxy_for_game_network(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                process = Mock()
                process.poll.return_value = None
                app.capture_process = process
                with patch("sheep_helper.guide_ui.game_client_rect", return_value=None):
                    app.events.put(("captured", "已抓到第二关地图"))
                    app.events.put(("ready", (board, plan, CARDS,
                                              Path(directory) / "progress.json", True)))
                    app._tick()
                self.assertTrue((Path(directory) / CAPTURE_DONE_MARKER).exists())
                self.assertEqual(app.capture_button["text"], "新局抓包")
                self.assertIs(app.capture_process, process)
                process.terminate.assert_not_called()
                app._begin_capture()
                self.assertFalse((Path(directory) / CAPTURE_DONE_MARKER).exists())
                self.assertIsNone(app.guide)
                self.assertEqual(app.capture_button["text"], "正在准备…")
                app.capture_process = None
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_auto_click_only_sends_one_board_click_after_opt_in(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json")
                app.last_rect = (1, 0, 0, 565, 1006)
                app.center_x.set(282)
                app.center_y.set(435)
                app.cell_px.set(52)
                app.calibrated = True
                app.auto_verified = True
                app._target_snapshot = Image.new("L", (20, 20), "white")
                app._target_bbox = (0, 0, 20, 20)
                app._snapshot_step = 0
                user32 = Mock()
                user32.GetForegroundWindow.return_value = 1
                user32.GetAsyncKeyState.return_value = 0
                user32.SetCursorPos.return_value = True
                app._maybe_auto_click(user32, 1, 0, 0, 565, 1006)
                user32.mouse_event.assert_not_called()
                app._toggle_auto_click()
                app.last_auto_click = time.monotonic() - 2
                app._maybe_auto_click(user32, 1, 0, 0, 565, 1006)
                self.assertEqual(user32.mouse_event.call_count, 2)
                self.assertEqual(app.confirm_button["state"], "disabled")
                app._maybe_auto_click(user32, 1, 0, 0, 565, 1006)
                self.assertEqual(user32.mouse_event.call_count, 2)
                app.guide = Mock(confirmed=0, next_action=("move_out", -1))
                app.pending_revive = False
                app._maybe_auto_click(user32, 1, 0, 0, 565, 1006)
                self.assertEqual(user32.mouse_event.call_count, 2)
                app.pending_revive = True
                app.auto_click_pending_step = -1
                app._pending_click_step = -1
                app._maybe_auto_click(user32, 1, 0, 0, 565, 1006)
                self.assertEqual(user32.mouse_event.call_count, 2)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_auto_click_stops_without_confirmed_visual_change(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json")
                app.last_rect = (1, 0, 0, 565, 1006)
                app.calibrated = True
                app.auto_verified = True
                app.auto_click_enabled = True
                app.auto_click_pending_step = 0
                before = Image.new("L", (20, 20), "white")
                with patch("sheep_helper.guide_ui.ImageGrab.grab", return_value=before.copy()):
                    app._verify_target_click(0, before, (0, 0, 20, 20))
                self.assertEqual(app.guide.confirmed, 0)
                self.assertFalse(app.auto_click_enabled)
                self.assertEqual(app.confirm_button["state"], "normal")
                self.assertIn("自动点击已停止", app.status.get())
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_dark_target_does_not_suppress_snapshot_or_auto_click(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, CARDS, Path(directory) / "progress.json")
                app.last_rect = (1, 0, 0, 565, 1006)
                app.center_x.set(282)
                app.center_y.set(435)
                app.cell_px.set(52)
                app.calibrated = True
                app.auto_verified = True
                app.auto_click_enabled = True
                with patch("sheep_helper.guide_ui.ImageGrab.grab",
                           return_value=Image.new("RGB", (26, 26), "#71845e")):
                    app._capture_target_snapshot()
                self.assertIsNotNone(app._target_snapshot)
                self.assertEqual(app._snapshot_step, app.guide.confirmed)
                self.assertTrue(app.auto_click_enabled)
                app._draw_overlay(565, 1006)
                self.assertIn("line", {app.canvas.type(item) for item in app.canvas.find_all()})
                user32 = Mock()
                user32.GetForegroundWindow.return_value = 1
                user32.GetAsyncKeyState.return_value = 0
                user32.SetCursorPos.return_value = True
                app.last_auto_click = time.monotonic() - 2
                app._maybe_auto_click(user32, 1, 0, 0, 565, 1006)
                self.assertEqual(user32.mouse_event.call_count, 2)
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_revive_and_move_out_need_their_own_completion_button(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792609938426" / "verified"
        cards = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards, next(folder.glob("*.coverage.json")))
        plan = load_verified_plan(board, next(folder.glob("*.guide-solution.json")))
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, cards, Path(directory) / "progress.json")
                for _ in range(171):
                    app.guide._advance(save=False)
                app.calibrated = True
                app._update_step()
                app._confirm()  # The overflowing board click triggers revive.
                self.assertTrue(app.pending_revive)
                self.assertEqual(app.confirm_button["state"], "disabled")
                self.assertEqual(app.special_button["text"], "确认已完成复活")
                self.assertEqual(app.guide.confirmed, 172)
                app._draw_overlay(565, 1006)
                self.assertNotIn("oval", {app.canvas.type(item) for item in app.canvas.find_all()})
                app.calibrated = False  # An ad can temporarily hide the game window.
                app._update_step()
                self.assertTrue(app.pending_revive)
                self.assertEqual(app.special_button["text"], "确认已完成复活")
                self.assertEqual(app.special_button["state"], "disabled")
                app.calibrated = True
                app._update_step()
                self.assertEqual(app.special_button["state"], "normal")
                app._confirm_special()
                self.assertFalse(app.pending_revive)
                self.assertFalse(app.auto_click_enabled)
                app._draw_overlay(565, 1006)
                self.assertIn("oval", {app.canvas.type(item) for item in app.canvas.find_all()})
                app._confirm()  # One more board pick, then move-out is required.
                self.assertEqual(app.guide.confirmed, 173)
                self.assertEqual(app.confirm_button["state"], "disabled")
                self.assertEqual(app.special_button["text"], "确认已使用移出道具")
                app._confirm()  # Generic board confirmation cannot skip the tool.
                self.assertEqual(app.guide.confirmed, 173)
                app._confirm_special()
                self.assertEqual(app.guide.confirmed, 174)
                self.assertEqual(app.special_button["text"], "确认已取回此牌")
            finally:
                app.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows guide only")
    def test_already_clicked_card_can_be_reconciled_once_manually(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792609938426" / "verified"
        cards = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards, next(folder.glob("*.coverage.json")))
        plan = load_verified_plan(board, next(folder.glob("*.guide-solution.json")))
        with tempfile.TemporaryDirectory() as directory:
            app = GuideApp(capture_dir=Path(directory), start_capture=False)
            try:
                app._set_guide(board, plan, cards, Path(directory) / "progress.json")
                for _ in range(187):
                    app.guide._advance(save=False)
                app.calibrated = True
                app._update_step()
                self.assertIn("剪刀", app.step_text.get())
                app._confirm()
                self.assertEqual(app.guide.confirmed, 188)
                self.assertIn("第 189 步", app.step_text.get())
            finally:
                app.close()

    def test_step_label_uses_verified_face_name_and_location(self) -> None:
        self.assertEqual(tuple(FACE_NAMES.values()), (
            "草丛", "萝卜", "玉米", "木桩", "草叉", "白菜", "毛球", "刷子",
            "剪刀", "奶瓶", "水桶", "手套", "铃铛", "火堆", "毛线", "稻草",
        ))
        self.assertEqual(face_name(8), "刷子")
        self.assertEqual(board_region(12, 12, min_col=4, max_col=52,
                                      min_row=0, max_row=60), "左上区域")

    def test_capture_skips_tutorial_map_in_game_start_order(self) -> None:
        tutorial = "f35bb0aff5ff7fef951795a377cf0749"
        second = "c18f6c3f109d7d51ac4dd5ee49a2ae4d"
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            (capture_dir / "seeds").mkdir()
            (capture_dir / "maps").mkdir()
            (capture_dir / "seeds" / "recovered-test.json").write_text(
                json.dumps({"map_md5": [tutorial, second], "map_seed": [1, 2, 3, 4]}),
                encoding="utf-8",
            )
            for map_id in (tutorial, second):
                source = ROOT / "captured-layouts" / f"{map_id}.map"
                if not source.is_file():
                    source = ROOT / "captured-layouts" / "maps" / source.name
                (capture_dir / "maps" / source.name).write_bytes(source.read_bytes())
            found = find_captured_game(capture_dir)
            self.assertIsNotNone(found)
            self.assertEqual(found.map_id, second)
            self.assertEqual(found.tile_count, 216)

    def test_capture_never_selects_first_stage_even_when_it_has_many_tiles(self) -> None:
        first = "c18f6c3f109d7d51ac4dd5ee49a2ae4d"
        second = "b729dfc98d8c6e516dddff3f68ae1381"
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            (capture_dir / "seeds").mkdir()
            (capture_dir / "maps").mkdir()
            (capture_dir / "seeds" / "recovered-test.json").write_text(
                json.dumps({"map_md5": [first, second], "map_seed": [1, 2, 3, 4]}),
                encoding="utf-8",
            )
            for map_id in (first, second):
                source = ROOT / "captured-layouts" / "maps" / f"{map_id}.map"
                (capture_dir / "maps" / source.name).write_bytes(source.read_bytes())
            found = find_captured_game(capture_dir)
            self.assertIsNotNone(found)
            self.assertEqual(found.map_id, second)
            (capture_dir / "maps" / f"{second}.map").unlink()
            self.assertIsNone(find_captured_game(capture_dir))

    def test_daily_map_with_matching_seed_can_be_discovered(self) -> None:
        map_id = "b729dfc98d8c6e516dddff3f68ae1381"
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            (capture_dir / "seeds").mkdir()
            (capture_dir / "maps").mkdir()
            (capture_dir / "seeds" / "recovered-daily.json").write_text(json.dumps({
                "map_md5": [map_id], "map_seed": [1, 2, 3, 4],
                "source_path": "/sheep/v1/game/daily/game_start",
            }), encoding="utf-8")
            source = ROOT / "captured-layouts" / "maps" / f"{map_id}.map"
            (capture_dir / "maps" / source.name).write_bytes(source.read_bytes())
            found = find_captured_game(capture_dir)
            self.assertIsNotNone(found)
            self.assertEqual(found.map_id, map_id)
            self.assertEqual(found.tile_count, 237)
            self.assertEqual(found.source_path, "/sheep/v1/game/daily/game_start")

    def test_tile_center_uses_eighth_cell_map_units(self) -> None:
        self.assertEqual(
            tile_center(20, 36, center_x=100, center_y=200, cell_px=40, mid_col=12, mid_row=20),
            (140, 280),
        )

    def test_verified_plan_and_resume_only_advance_confirmed_actions(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        plan = load_verified_plan(board, SOLUTION)
        with tempfile.TemporaryDirectory() as directory:
            progress = Path(directory) / "progress.json"
            guide = GuidedPlay(board, plan, progress)
            first = guide.next_action
            self.assertIsNotNone(first)
            guide.confirm_next()
            guide.confirm_next()
            self.assertEqual(guide.confirmed, 2)
            restored = GuidedPlay(board, plan, progress)
            restored.resume()
            self.assertEqual(restored.confirmed, 2)
            self.assertEqual(restored.next_action, guide.next_action)
            self.assertEqual(restored.session.state, guide.session.state)
            while not restored.finished:
                restored.confirm_next()
            self.assertEqual(restored.session.state.remaining, 0)
            self.assertEqual(restored.session.state.tray, ())

    def test_partial_or_mismatched_route_is_never_offered(self) -> None:
        board = Board.from_files(CARDS, COVERAGE)
        record = json.loads(SOLUTION.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "solution.json"
            record["solved"] = False
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "完整通关路线"):
                load_verified_plan(board, path)
            record["solved"] = True
            record["actions"][0]["type"] = -1
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "当前局不一致"):
                load_verified_plan(board, path)

    def test_discovery_requires_a_matching_seed_and_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            (capture_dir / "seeds").mkdir()
            (capture_dir / "maps").mkdir()
            (capture_dir / "seeds" / "recovered-1.json").write_text(
                json.dumps({"map_md5": [MAP_ID], "map_seed": [1, 2, 3, 4]}), encoding="utf-8"
            )
            self.assertIsNone(find_captured_game(capture_dir, min_tiles=1))
            source = ROOT / "captured-layouts" / f"{MAP_ID}.map"
            (capture_dir / "maps" / source.name).write_bytes(source.read_bytes())
            found = find_captured_game(capture_dir, min_tiles=1)
            self.assertIsNotNone(found)
            self.assertEqual(found.map_id, MAP_ID)
            self.assertEqual(found.tile_count, 18)

    def test_timeout_never_writes_a_partial_guide_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "guide-solution.json"
            capture = CapturedGame(Path(directory) / "seed.json", Path(directory) / "map.map", MAP_ID, 80002, 18)
            prepared = PreparedGame(capture, Path(directory) / "layout.json", COVERAGE, CARDS, output)
            partial = SolveResult(False, (("board", 0),), 10, None, 0.0, "budget exhausted")
            with patch("sheep_helper.live.solve_hybrid", return_value=partial):
                with self.assertRaisesRegex(RuntimeError, "建议重开关卡"):
                    solve_prepared_game(prepared, timeout=0.01)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
