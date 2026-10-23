import unittest

from sheep_helper.extractor import extract_candidates
from sheep_helper.redaction import redact


class ExtractorTests(unittest.TestCase):
    def test_extracts_tiles_and_parent_slot(self) -> None:
        payload = {
            "token": "do-not-keep",
            "game": {
                "slot": [{"type": "apple"}],
                "tiles": [
                    {"id": index, "type": "apple" if index % 2 else "pear", "x": index, "y": index + 1, "layer": 2}
                    for index in range(9)
                ],
            },
        }
        candidates = extract_candidates(redact(payload))
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.path, "$.game.tiles")
        self.assertEqual(candidate.slot, ["apple"])
        self.assertEqual(len(candidate.tiles), 9)
        self.assertGreaterEqual(candidate.score, 14)

    def test_redaction_removes_sensitive_fields_recursively(self) -> None:
        self.assertEqual(redact({"token": "x", "tiles": [{"type": "a", "sign": "y"}]}), {"tiles": [{"type": "a"}]})
