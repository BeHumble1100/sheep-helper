from __future__ import annotations

import base64
import unittest

from sheep_helper.match_data import extract_runtime_cards


# Produced by the game's own _0x5cc365 UTF-16 compressor from a one-card
# FirstGameStateSyncV0 snapshot, independent of this Python decoder.
CLIENT_UTF16_SAMPLE = (
    "YxsxPOwBgAglWzkYNyC3FCUYNGfRAWJgJxwsWDowMmB3QJYAJCTgW3UEuhQn"
    "QyIUAD0ABUUHMyNDAC0AKQw8EUIhIRhFGTB5kwfgTOkMuxQjQCZdNgB/AH0AEwogICAA"
)


class MatchDataTests(unittest.TestCase):
    def test_client_snapshot_yields_exact_card(self) -> None:
        encoded = base64.b64decode(CLIENT_UTF16_SAMPLE).decode("utf-16-le")
        self.assertEqual(extract_runtime_cards(encoded, ["a" * 32]), {
            "a" * 32: [{"id": "1-2-3", "type": 4}],
        })

    def test_bad_snapshot_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            extract_runtime_cards("not a compressed snapshot", ["a" * 32])


if __name__ == "__main__":
    unittest.main()
