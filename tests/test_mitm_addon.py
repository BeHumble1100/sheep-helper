import json
import tempfile
import unittest
from pathlib import Path

from sheep_helper.mitm_addon import LayoutOnlyCapture


class _Response:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.content = json.dumps(payload).encode("utf-8")

    def get_text(self, strict=False):
        return self.content.decode("utf-8")


class _Request:
    pretty_url = "https://example.invalid/layout?token=secret"


class _Flow:
    def __init__(self, payload):
        self.request = _Request()
        self.response = _Response(payload)


class AddonTests(unittest.TestCase):
    def test_writes_only_redacted_layout_candidate(self):
        payload = {
            "token": "secret",
            "game": {
                "tiles": [
                    {"id": number, "type": "a" if number % 2 else "b", "x": number, "y": number, "layer": 1}
                    for number in range(6)
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            capture = LayoutOnlyCapture(directory)
            capture.response(_Flow(payload))
            files = list(Path(directory).glob("layout-candidate-*.json"))
            self.assertEqual(len(files), 1)
            result = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(result["source_url"], "https://example.invalid/layout")
            self.assertNotIn("token", files[0].read_text(encoding="utf-8"))
            self.assertEqual(result["candidates"][0]["tile_count"], 6)
            status = json.loads((Path(directory) / "capture-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["candidate_responses"], 1)
