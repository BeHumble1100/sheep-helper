"""Verify that captured card faces, geometry, and runtime sample agree."""

from __future__ import annotations

import collections
import json
from pathlib import Path


ROOT = Path("captured-layouts")
SPECS = (
    ("f35bb0aff5ff7fef951795a377cf0749", 18),
    ("64510ca5ca10058da0ca36f5d02fe9bf", 225),
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    reports = []
    for digest, expected_count in SPECS:
        layout = load_json(ROOT / f"{digest}.layout.json")
        cards = load_json(ROOT / f"{digest}.cards.json")
        coverage = load_json(ROOT / f"{digest}.coverage.json")
        layout_ids = {
            tile["id"]
            for tiles in layout["levels"].values()
            for tile in tiles
        }
        card_ids = {tile["id"] for tile in cards["tiles"]}
        coverage_ids = {tile["id"] for tile in coverage["tiles"]}
        assert len(cards["tiles"]) == expected_count
        assert layout_ids == card_ids == coverage_ids
        assert all(
            tile["face_resource"] == f"block/daily/block_{tile['type']}"
            for tile in cards["tiles"]
        )
        reports.append(
            {
                "map": digest,
                "tiles": expected_count,
                "initially_visible": len(coverage["initially_visible"]),
                "face_types": dict(
                    sorted(collections.Counter(
                        tile["type"] for tile in cards["tiles"]
                    ).items())
                ),
            }
        )

    runtime = load_json(ROOT / "runtime-map-probe.json")
    first_cards = load_json(ROOT / f"{SPECS[0][0]}.cards.json")
    actual = {entry["data"]["id"]: entry["data"]["type"] for entry in runtime["cards"]}
    expected = {entry["id"]: entry["type"] for entry in first_cards["tiles"]}
    assert actual == expected
    print(json.dumps({
        "status": "verified",
        "maps": reports,
        "runtime_first_map_exact_match": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
