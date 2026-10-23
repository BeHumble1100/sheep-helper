from __future__ import annotations

import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from sheep_helper.vision import find_alignment, find_daily_alignment, is_exposed_card, visibly_changed
from sheep_helper.solver import Board


ROOT = Path(__file__).resolve().parents[1]


class VisionTests(unittest.TestCase):
    def test_alignment_finds_visible_card_grid(self) -> None:
        image = Image.new("RGB", (400, 700), "#454c5b")
        draw = ImageDraw.Draw(image)
        points = [(0, 0), (8, 0), (20, 4), (4, 8), (24, 12),
                  (12, 16), (32, 16), (0, 24), (16, 28), (28, 32)]
        for col, row in points:
            x = 235 + (col - 16) * 50 / 8
            y = 310 + (row - 16) * 50 / 8
            draw.rectangle((x - 21, y - 21, x + 21, y + 21),
                           fill="#f1f0dc", outline="#242b35", width=3)
        result = find_alignment(image, points, mid_col=16, mid_row=16)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 235), 5)
        self.assertLessEqual(abs(result.center_y - 310), 5)
        self.assertLessEqual(abs(result.cell_px - 50), 4)

    def test_uninformative_screenshot_is_not_trusted(self) -> None:
        image = Image.new("RGB", (400, 700), "#454c5b")
        points = [(0, 0), (8, 8), (16, 16), (24, 24), (32, 32)]
        self.assertIsNone(find_alignment(image, points, mid_col=16, mid_row=16))

    def test_off_center_card_pattern_is_not_accepted_as_board(self) -> None:
        image = Image.new("RGB", (400, 700), "#c5ff90")
        draw = ImageDraw.Draw(image)
        points = [(0, 0), (8, 0), (20, 4), (4, 8), (24, 12),
                  (12, 16), (32, 16), (0, 24), (16, 28), (28, 32)]
        for col, row in points:
            x = 110 + (col - 16) * 50 / 8
            y = 310 + (row - 16) * 50 / 8
            draw.rectangle((x - 21, y - 21, x + 21, y + 21),
                           fill="#f1f0dc", outline="#242b35", width=3)
        self.assertIsNone(find_alignment(image, points, mid_col=16, mid_row=16))

    def test_tutorial_cards_cannot_align_to_second_level_map(self) -> None:
        image = Image.new("RGB", (565, 1006), "#c5ff90")
        draw = ImageDraw.Draw(image)
        tutorial_points = [(12, 20), (20, 20), (36, 20), (44, 20),
                           (20, 41), (28, 41), (36, 41)]
        for col, row in tutorial_points:
            x = 282 + (col - 28) * 58 / 8
            y = 383 + (row - 20) * 58 / 8
            draw.rectangle((x - 25, y - 25, x + 25, y + 25),
                           fill="#f1f0dc", outline="#242b35", width=3)
        folder = ROOT / "captured-layouts" / "session-1792606697088" / "verified"
        cards_path = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards_path, next(folder.glob("*.coverage.json")))
        cards = {tile["id"]: tile for tile in json.loads(cards_path.read_text(encoding="utf-8"))["tiles"]}
        points = [(cards[board.ids[index]]["col"], cards[board.ids[index]]["row"])
                  for index in range(board.size) if board.initial_available & (1 << index)]
        self.assertIsNone(find_alignment(image, points, mid_col=28, mid_row=30))

    def test_second_level_card_pattern_is_recognized(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792606697088" / "verified"
        cards_path = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards_path, next(folder.glob("*.coverage.json")))
        cards = {tile["id"]: tile for tile in json.loads(cards_path.read_text(encoding="utf-8"))["tiles"]}
        points = [(cards[board.ids[index]]["col"], cards[board.ids[index]]["row"])
                  for index in range(board.size) if board.initial_available & (1 << index)]
        # game_client_rect excludes the desktop mini-program title bar.
        image = Image.new("RGB", (565, 1006), "#c5ff90")
        draw = ImageDraw.Draw(image)
        for col, row in points:
            x = 282 + (col - 28) * 52 / 8
            y = 435 + (row - 30) * 52 / 8
            draw.rectangle((x - 22, y - 22, x + 22, y + 22),
                           fill="#f1f0dc", outline="#242b35", width=3)
        result = find_alignment(image, points, mid_col=28, mid_row=30)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 282), 7)
        self.assertLessEqual(abs(result.center_y - 435), 7)
        self.assertLessEqual(abs(result.cell_px - 52), 5)

    def test_exposed_matcher_fits_icon_heavy_world_board(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792606697088" / "verified"
        cards_path = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards_path, next(folder.glob("*.coverage.json")))
        cards = {tile["id"]: tile for tile in json.loads(cards_path.read_text(encoding="utf-8"))["tiles"]}
        points = [(cards[board.ids[index]]["col"], cards[board.ids[index]]["row"])
                  for index in range(board.size) if board.initial_available & (1 << index)]
        mid_col = (min(tile["col"] for tile in cards.values())
                   + max(tile["col"] for tile in cards.values())) / 2
        mid_row = (min(tile["row"] for tile in cards.values())
                   + max(tile["row"] for tile in cards.values())) / 2
        image = Image.new("RGB", (378, 672), "#c5ff90")
        draw = ImageDraw.Draw(image)
        for col, row in points:
            x = round(189 + (col - mid_col) * 34 / 8)
            y = round(285 + (row - mid_row) * 34 / 8)
            draw.rectangle((x - 16, y - 16, x + 16, y + 16),
                           fill="#f1f0dc", outline="#242b35", width=2)
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), fill="#e56b20")
        result = find_daily_alignment(image, points, mid_col=mid_col, mid_row=mid_row)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 189), 6)
        self.assertLessEqual(abs(result.center_y - 285), 6)
        self.assertLessEqual(abs(result.cell_px - 34), 3)

    def test_daily_card_pattern_is_recognized_without_world_calibration(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792612869064" / "verified"
        cards_path = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards_path, next(folder.glob("*.coverage.json")))
        cards = {tile["id"]: tile for tile in json.loads(cards_path.read_text(encoding="utf-8"))["tiles"]}
        points = [(cards[board.ids[index]]["col"], cards[board.ids[index]]["row"])
                  for index in range(board.size) if board.initial_available & (1 << index)]
        image = Image.new("RGB", (565, 1006), "#c5ff90")
        draw = ImageDraw.Draw(image)
        for col, row in points:
            x = 282 + (col - 28) * 52 / 8
            y = 490 + (row - 32) * 52 / 8
            draw.rectangle((x - 22, y - 22, x + 22, y + 22),
                           fill="#f1f0dc", outline="#242b35", width=3)
        result = find_alignment(image, points, mid_col=28, mid_row=32)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 282), 5)
        self.assertLessEqual(abs(result.center_y - 490), 5)
        self.assertLessEqual(abs(result.cell_px - 52), 4)

    def test_daily_alignment_uses_bright_cards_anywhere_on_map(self) -> None:
        image = Image.new("RGB", (378, 715), "#c5ff90")
        draw = ImageDraw.Draw(image)
        points = [(8, 8), (16, 8), (32, 12), (48, 16), (12, 24), (28, 28),
                  (40, 32), (8, 40), (20, 48), (36, 52), (44, 60), (24, 64)]
        dark = {(16, 8), (48, 16), (28, 28), (8, 40)}
        for col, row in points:
            x = round(188 + (col - 28) * 34 / 8)
            y = round(338 + (row - 32) * 34 / 8)
            draw.rectangle((x - 17, y - 17, x + 17, y + 17),
                           fill="#70834e" if (col, row) in dark else "#f1f0dc",
                           outline="#242b35", width=2)
            if (col, row) not in dark:
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#ee8428")
        result = find_daily_alignment(image, points, mid_col=28, mid_row=32)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 188), 5)
        self.assertLessEqual(abs(result.center_y - 338), 4, result)
        self.assertLessEqual(abs(result.cell_px - 34), 2)
        self.assertIsNone(find_daily_alignment(Image.new("RGB", (378, 715), "#c5ff90"),
                                               points, mid_col=28, mid_row=32))

    def test_daily_alignment_does_not_need_a_bottom_row_of_four(self) -> None:
        image = Image.new("RGB", (378, 715), "#c5ff90")
        draw = ImageDraw.Draw(image)
        points = [(8, 8), (40, 16), (16, 48), (44, 64)]
        for col, row in points:
            x = round(188 + (col - 26) * 34 / 8)
            y = round(338 + (row - 36) * 34 / 8)
            draw.rectangle((x - 17, y - 17, x + 17, y + 17),
                           fill="#f1f0dc", outline="#242b35", width=2)
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#ee8428")
        result = find_daily_alignment(image, points, mid_col=26, mid_row=36)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 188), 5)
        self.assertLessEqual(abs(result.center_y - 338), 5)

    def test_daily_alignment_scales_to_larger_game_window(self) -> None:
        image = Image.new("RGB", (565, 1006), "#c5ff90")
        draw = ImageDraw.Draw(image)
        points = [(8, 8), (16, 8), (32, 12), (48, 16), (12, 24), (28, 28),
                  (40, 32), (8, 40), (20, 48), (36, 52), (44, 60), (24, 64)]
        dark = {(16, 8), (48, 16), (28, 28), (8, 40)}
        for col, row in points:
            x = round(282 + (col - 28) * 52 / 8)
            y = round(490 + (row - 32) * 52 / 8)
            draw.rectangle((x - 26, y - 26, x + 26, y + 26),
                           fill="#70834e" if (col, row) in dark else "#f1f0dc",
                           outline="#242b35", width=3)
            if (col, row) not in dark:
                draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill="#ee8428")
        result = find_daily_alignment(image, points, mid_col=28, mid_row=32)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center_x - 282), 6)
        self.assertLessEqual(abs(result.center_y - 490), 6)
        self.assertLessEqual(abs(result.cell_px - 52), 3)

    def test_daily_alignment_rejects_a_different_board(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792645497862" / "verified"
        cards_path = next(folder.glob("*.cards.json"))
        board = Board.from_files(cards_path, next(folder.glob("*.coverage.json")))
        cards = {tile["id"]: tile for tile in json.loads(cards_path.read_text(encoding="utf-8"))["tiles"]}
        points = [(cards[board.ids[index]]["col"], cards[board.ids[index]]["row"])
                  for index in range(board.size) if board.initial_available & (1 << index)]
        image = Image.new("RGB", (378, 715), "#c5ff90")
        draw = ImageDraw.Draw(image)
        for col, row in [(12, 20), (20, 20), (36, 20), (44, 20),
                         (20, 41), (28, 41), (36, 41)]:
            x = round(188 + (col - 28) * 34 / 8)
            y = round(338 + (row - 30) * 34 / 8)
            draw.rectangle((x - 17, y - 17, x + 17, y + 17),
                           fill="#f1f0dc", outline="#242b35", width=2)
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#ee8428")
        self.assertIsNone(find_daily_alignment(image, points, mid_col=28, mid_row=32))

    def test_click_change_requires_substantial_visual_difference(self) -> None:
        before = Image.new("RGB", (40, 40), "white")
        after = Image.new("RGB", (40, 40), "black")
        self.assertTrue(visibly_changed(before, after))
        self.assertFalse(visibly_changed(before, before.copy()))

    def test_daily_target_must_be_an_exposed_card_not_empty_grass(self) -> None:
        image = Image.new("RGB", (200, 220), "#c5ff90")
        draw = ImageDraw.Draw(image)
        draw.rectangle((75, 75, 125, 125), fill="#f1f0dc", outline="#242b35", width=3)
        self.assertTrue(is_exposed_card(image, 100, 100, 52))
        self.assertFalse(is_exposed_card(image, 45, 45, 52))
