import json
import tempfile
import unittest
from pathlib import Path

from sheep_helper.har import iter_har_payloads


class HarTests(unittest.TestCase):
    def test_har_payload_is_redacted_and_query_is_removed(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {"url": "https://example.invalid/game?token=secret"},
                        "response": {"content": {"text": json.dumps({"token": "secret", "tiles": []})}},
                    }
                ]
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.har"
            path.write_text(json.dumps(har), encoding="utf-8")
            self.assertEqual(list(iter_har_payloads(path)), [("https://example.invalid/game", {"tiles": []})])
