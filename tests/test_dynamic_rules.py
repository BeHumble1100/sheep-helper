from pathlib import Path
import unittest

from tools.check_dynamic_plan import check


ROOT = Path(__file__).resolve().parents[1]


class DynamicRulesTests(unittest.TestCase):
    def test_color_change_invalidates_static_route(self) -> None:
        folder = ROOT / "captured-layouts" / "session-1792737599293" / "verified"
        cards = next(folder.glob("*.cards.json"))
        first_change, outcome = check(
            cards,
            Path(str(cards).replace(".cards.json", ".coverage.json")),
            Path(str(cards).replace(".cards.json", ".guide-solution.json")),
        )
        self.assertEqual(first_change, 37)
        self.assertIn("第 49 步", outcome)


if __name__ == "__main__":
    unittest.main()
