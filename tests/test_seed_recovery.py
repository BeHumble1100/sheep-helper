from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from sheep_helper.seed_recovery_addon import KNOWN_TOKEN, SeedRecovery, _seed_message
from sheep_helper.game_capture_addon import CAPTURE_DONE_MARKER


class Request:
    pretty_host = "cat-match.easygame2021.com"

    def __init__(self, path: str, body: str = "") -> None:
        self.path = path
        self.body = body
        self.headers = {"content-type": "application/json"}

    def get_text(self) -> str:
        return self.body

    def set_text(self, value: str) -> None:
        self.body = value


class Response:
    status_code = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def get_text(self) -> str:
        return self.body.decode("utf-8")

    def set_text(self, value: str) -> None:
        self.body = value.encode("utf-8")

    def get_content(self, strict: bool = False) -> bytes:
        return self.body


class Flow:
    def __init__(self, path: str, body: bytes) -> None:
        self.request = Request(path)
        self.response = Response(body)
        self.metadata: dict[str, object] = {}


class SeedRecoveryTests(unittest.TestCase):
    def test_completed_capture_does_not_modify_a_new_game_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            addon = SeedRecovery(directory)
            Path(directory, CAPTURE_DONE_MARKER).touch()
            start = Flow("/sheep/v1/game/world/game_start", json.dumps({
                "data": {"map_seed_2": "original-token", "need_seed": True}
            }).encode())
            addon.response(start)
            self.assertEqual(json.loads(start.response.get_text())["data"]["map_seed_2"],
                             "original-token")
            self.assertIsNone(addon.pending)

    def test_known_plaintext_probe_restores_request_and_saves_only_seed(self) -> None:
        token = "original-token"
        map_id = "a" * 32
        stream = bytes(range(len(_seed_message(KNOWN_TOKEN))))
        with tempfile.TemporaryDirectory() as directory:
            addon = SeedRecovery(directory)
            start = Flow("/sheep/v1/game/world/game_start", json.dumps({
                "data": {"map_seed_2": token, "need_seed": True, "map_md5": [map_id],
                         "match_data": "compressed-snapshot"}
            }).encode())
            start.metadata["sheep_runtime_cards_file"] = "runtime-123.json"
            addon.response(start)
            self.assertEqual(json.loads(start.response.get_text())["data"]["map_seed_2"], KNOWN_TOKEN)

            known = _seed_message(KNOWN_TOKEN)
            encrypted = bytes(left ^ right for left, right in zip(known, stream))
            request = Flow("/sheep/v1/game/map_info_ex_seed", b"")
            request.request.body = json.dumps({"info": base64.b64encode(b"\x01\x00\x00" + encrypted).decode()})
            addon.request(request)
            forwarded = base64.b64decode(json.loads(request.request.body)["info"])
            original = _seed_message(token)
            self.assertEqual(forwarded[3:], bytes(left ^ right for left, right in zip(original, stream)))

            plain_response = bytes([8, 1, 16, 1, 16, 2, 16, 3, 16, 4])
            request.response = Response(bytes(left ^ right for left, right in zip(plain_response, stream)))
            addon.response(request)
            saved = list((Path(directory) / "seeds").glob("*.json"))
            self.assertEqual(len(saved), 1)
            record = json.loads(saved[0].read_text(encoding="utf-8"))
            self.assertEqual(record["map_seed"], [1, 2, 3, 4])
            self.assertEqual(record["map_md5"], [map_id])
            self.assertEqual(record["runtime_cards_file"], "runtime-123.json")
            self.assertTrue(record["match_data_present"])
            self.assertNotIn(token, saved[0].read_text(encoding="utf-8"))

    def test_daily_start_uses_same_seed_probe_when_fields_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            addon = SeedRecovery(directory)
            start = Flow("/sheep/v1/game/daily/game_start", json.dumps({
                "data": {"map_seed_2": "daily-token", "need_seed": True,
                         "map_md5": ["b" * 32]}
            }).encode())
            addon.response(start)
            self.assertEqual(json.loads(start.response.get_text())["data"]["map_seed_2"], KNOWN_TOKEN)
            self.assertEqual(addon.pending[2], "/sheep/v1/game/daily/game_start")


if __name__ == "__main__":
    unittest.main()
