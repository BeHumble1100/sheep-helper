from __future__ import annotations

import json
import base64
import tempfile
import unittest
from pathlib import Path

from sheep_helper.card_types import restore_cards
from sheep_helper.game_capture_addon import CAPTURE_DONE_MARKER, GameAssetCapture
from sheep_helper.map_format import decode_map
from sheep_helper.pipeline import assemble_session, validate_seed_record, write_session
from sheep_helper.solver import Board
from test_match_data import CLIENT_UTF16_SAMPLE


ROOT = Path(__file__).resolve().parents[1]


class _Request:
    def __init__(self, path: str) -> None:
        self.path = path
        self.pretty_host = "cat-match-static.easygame2021.com"


class _Response:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.content = body
        self.headers = {"content-type": content_type}
        self.status_code = 200

    def get_content(self, strict: bool = False) -> bytes:
        return self.content

    def get_text(self, strict: bool = False) -> str:
        return self.content.decode("utf-8")


class _Flow:
    def __init__(self, path: str, body: bytes, content_type: str) -> None:
        self.request = _Request(path)
        self.response = _Response(body, content_type)


class GamePipelineTests(unittest.TestCase):
    def test_daily_start_plain_seed_is_captured_without_response_values_in_diagnostics(self) -> None:
        map_id = "b729dfc98d8c6e516dddff3f68ae1381"
        path = "/sheep/v1/game/daily/game_start?token=private"
        payload = {"data": {"map_md5": map_id, "map_seed": [1, 2, 3, 4],
                            "need_seed": False, "private_value": "secret"}}
        with tempfile.TemporaryDirectory() as directory:
            capture = GameAssetCapture(directory)
            capture.response(_Flow(path, json.dumps(payload).encode(), "application/json"))
            folder = Path(directory)
            record = json.loads((folder / "latest-game-start.json").read_text(encoding="utf-8"))
            self.assertEqual(record["source_path"], "/sheep/v1/game/daily/game_start")
            seed = json.loads(next((folder / "seeds").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(seed["map_md5"], [map_id])
            self.assertEqual(seed["map_seed"], [1, 2, 3, 4])
            status = (folder / "game-capture-status.json").read_text(encoding="utf-8")
            self.assertIn("/sheep/v1/game/daily/game_start", status)
            self.assertNotIn("private_value\": \"secret", status)
            self.assertNotIn("token=private", status)

    def test_game_start_extracts_runtime_cards_without_saving_raw_match_data(self) -> None:
        map_id = "a" * 32
        snapshot = base64.b64decode(CLIENT_UTF16_SAMPLE).decode("utf-16-le")
        payload = {"data": {"map_md5": [map_id], "map_seed": [1, 2, 3, 4],
                            "need_seed": False, "match_data": snapshot}}
        with tempfile.TemporaryDirectory() as directory:
            capture = GameAssetCapture(directory)
            capture.response(_Flow("/sheep/v1/game/map_info_ex",
                                   json.dumps(payload).encode(), "application/json"))
            folder = Path(directory)
            record = json.loads((folder / "latest-game-start.json").read_text(encoding="utf-8"))
            runtime = json.loads((folder / "runtime" / record["runtime_cards_file"]).read_text(encoding="utf-8"))
            self.assertEqual(runtime[map_id], [{"id": "1-2-3", "type": 4}])
            self.assertTrue(record["match_data_present"])
            self.assertNotIn(snapshot, (folder / "latest-game-start.json").read_text(encoding="utf-8"))

    def test_completed_capture_does_not_write_later_map_assets(self) -> None:
        raw = (ROOT / "captured-layouts" / "64510ca5ca10058da0ca36f5d02fe9bf.map").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / CAPTURE_DONE_MARKER).touch()
            capture = GameAssetCapture(directory)
            capture.response(_Flow("/maps/64510ca5ca10058da0ca36f5d02fe9bf.map",
                                   raw, "application/octet-stream"))
            self.assertFalse((folder / "maps").exists())

    def test_map_asset_capture_ignores_unrelated_json(self) -> None:
        raw = (ROOT / "captured-layouts" / "64510ca5ca10058da0ca36f5d02fe9bf.map").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            capture = GameAssetCapture(directory)
            capture.response(_Flow("/cps/list", b'{"data":{"list":[]}}', "application/json"))
            self.assertFalse(list(Path(directory).glob("layout-candidate-*.json")))
            capture.response(_Flow("/maps/64510ca5ca10058da0ca36f5d02fe9bf.map", raw, "application/octet-stream"))
            saved = Path(directory) / "maps" / "64510ca5ca10058da0ca36f5d02fe9bf.map"
            self.assertEqual(saved.read_bytes(), raw)

    def test_decoded_map_and_seed_rebuild_match_captured_faces(self) -> None:
        raw = (ROOT / "captured-layouts" / "64510ca5ca10058da0ca36f5d02fe9bf.map").read_bytes()
        captured = json.loads((ROOT / "captured-layouts" / "64510ca5ca10058da0ca36f5d02fe9bf.cards.json").read_text(encoding="utf-8"))
        restored = restore_cards(decode_map(raw), captured["map_seed"])
        expected = {tile["id"]: tile["type"] for tile in captured["tiles"]}
        actual = {tile["id"]: tile["type"] for tile in restored["tiles"]}
        self.assertEqual(actual, expected)

    def test_server_runtime_faces_override_seed_only_for_exact_same_map(self) -> None:
        map_id = "64510ca5ca10058da0ca36f5d02fe9bf"
        source = ROOT / "captured-layouts" / f"{map_id}.map"
        historical = json.loads((ROOT / "captured-layouts" / f"{map_id}.cards.json").read_text(encoding="utf-8"))
        runtime_tiles = [{"id": tile["id"], "type": tile["type"]} for tile in historical["tiles"]]
        other = next(index for index, tile in enumerate(runtime_tiles)
                     if tile["type"] != runtime_tiles[0]["type"])
        runtime_tiles[0]["type"], runtime_tiles[other]["type"] = (
            runtime_tiles[other]["type"], runtime_tiles[0]["type"]
        )
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "seeds").mkdir()
            (folder / "runtime").mkdir()
            seed_path = folder / "seeds" / "recovered-123.json"
            seed_path.write_text(json.dumps({"map_md5": [map_id],
                                             "map_seed": historical["map_seed"],
                                             "match_data_present": True,
                                             "runtime_cards_file": "runtime-123.json"}), encoding="utf-8")
            (folder / "runtime" / "runtime-123.json").write_text(
                json.dumps({map_id: runtime_tiles}), encoding="utf-8"
            )
            _, _, cards = assemble_session(source, seed_path)
            self.assertEqual(cards["verification_mode"], "server-runtime")
            self.assertEqual(cards["tiles"][0]["type"], runtime_tiles[0]["type"])
            runtime_tiles.pop()
            (folder / "runtime" / "runtime-123.json").write_text(
                json.dumps({map_id: runtime_tiles}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "坐标不一致"):
                assemble_session(source, seed_path)

    def test_session_pipeline_matches_historical_cards_and_coverage(self) -> None:
        map_id = "64510ca5ca10058da0ca36f5d02fe9bf"
        source = ROOT / "captured-layouts" / f"{map_id}.map"
        historical = json.loads((ROOT / "captured-layouts" / f"{map_id}.cards.json").read_text(encoding="utf-8"))
        prior_coverage = json.loads((ROOT / "captured-layouts" / f"{map_id}.coverage.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            seed_path = folder / "seed.json"
            seed_path.write_text(json.dumps({"map_md5": [map_id], "map_seed": historical["map_seed"]}), encoding="utf-8")
            layout_path, coverage_path, cards_path = write_session(source, seed_path, folder)
            cards = json.loads(cards_path.read_text(encoding="utf-8"))
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(len(cards["tiles"]), 225)
            self.assertEqual({tile["id"]: tile["type"] for tile in cards["tiles"]},
                             {tile["id"]: tile["type"] for tile in historical["tiles"]})
            self.assertEqual({tile["id"]: tile["blockers"] for tile in coverage["tiles"]},
                             {tile["id"]: tile["blockers"] for tile in prior_coverage["tiles"]})
            self.assertEqual(Board.from_files(cards_path, coverage_path).size, 225)
            self.assertEqual(json.loads(layout_path.read_text(encoding="utf-8"))["source_map_md5"], map_id)
            cards["map_seed"] = [0, 0, 0, 0]
            cards_path.write_text(json.dumps(cards), encoding="utf-8")
            with self.assertRaises(ValueError):
                Board.from_files(cards_path, coverage_path)
            with self.assertRaises(FileExistsError):
                write_session(source, seed_path, folder)

    def test_zero_or_cross_map_seed_is_rejected_before_writing(self) -> None:
        map_id = "64510ca5ca10058da0ca36f5d02fe9bf"
        source = ROOT / "captured-layouts" / f"{map_id}.map"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            seed_path = folder / "seed.json"
            for record in (
                {"map_md5": [map_id], "map_seed": [0, 0, 0, 0]},
                {"map_md5": ["f" * 32], "map_seed": [1, 2, 3, 4]},
            ):
                seed_path.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaises(ValueError):
                    write_session(source, seed_path, folder)
            self.assertFalse(list(folder.glob("*.cards.json")))

    def test_observed_card_mismatch_is_rejected(self) -> None:
        map_id = "64510ca5ca10058da0ca36f5d02fe9bf"
        source = ROOT / "captured-layouts" / f"{map_id}.map"
        historical = json.loads((ROOT / "captured-layouts" / f"{map_id}.cards.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            seed_path = folder / "seed.json"
            seen_path = folder / "seen.json"
            seed_path.write_text(json.dumps({"map_md5": [map_id], "map_seed": historical["map_seed"]}), encoding="utf-8")
            first = historical["tiles"][0]
            seen_path.write_text(json.dumps({str(first["id"]): first["type"] + 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                assemble_session(source, seed_path, seen_path)
            seen_path.write_text(json.dumps({str(first["id"]): first["type"]}), encoding="utf-8")
            _, _, cards = assemble_session(source, seed_path, seen_path)
            self.assertEqual(cards["verified_visible_ids"], [first["id"]])

    def test_visual_face_groups_check_repeated_and_distinct_icons(self) -> None:
        map_id = "64510ca5ca10058da0ca36f5d02fe9bf"
        source = ROOT / "captured-layouts" / f"{map_id}.map"
        historical = json.loads((ROOT / "captured-layouts" / f"{map_id}.cards.json").read_text(encoding="utf-8"))
        by_type: dict[int, list[str]] = {}
        for tile in historical["tiles"]:
            by_type.setdefault(tile["type"], []).append(tile["id"])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            seed_path = folder / "seed.json"
            seen_path = folder / "seen.json"
            seed_path.write_text(json.dumps({"map_md5": [map_id], "map_seed": historical["map_seed"]}), encoding="utf-8")
            observed = {by_type[1][0]: "icon-a", by_type[1][1]: "icon-a", by_type[2][0]: "icon-b"}
            seen_path.write_text(json.dumps(observed), encoding="utf-8")
            _, _, cards = assemble_session(source, seed_path, seen_path)
            self.assertEqual(cards["verification_mode"], "visual-grouping")
            self.assertEqual(cards["verified_visible_count"], 3)
            observed[by_type[1][1]] = "icon-b"
            seen_path.write_text(json.dumps(observed), encoding="utf-8")
            with self.assertRaises(ValueError):
                assemble_session(source, seed_path, seen_path)

    def test_seed_validator_rejects_boolean_and_out_of_range(self) -> None:
        for seed in ([True, 2, 3, 4], [-1, 2, 3, 4], [0x100000000, 2, 3, 4]):
            with self.assertRaises(ValueError):
                validate_seed_record({"map_md5": "a" * 32, "map_seed": seed}, "a" * 32)


if __name__ == "__main__":
    unittest.main()
