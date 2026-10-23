"""Turn a decoded GameMap into its exact client-side visibility graph."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


BLOCK_RANGE = 8  # BoardUI.minBlockNum in the unpacked game client.


def overlaps(lower: dict[str, object], upper: dict[str, object]) -> bool:
    """Match BoardUI.checkIsShow: strict bounds, including its edge behavior."""
    return (
        abs(int(lower["col"]) - int(upper["col"])) < BLOCK_RANGE
        and abs(int(lower["row"]) - int(upper["row"])) < BLOCK_RANGE
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="*.layout.json from decode_game_map.py")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    layout = json.loads(args.source.read_text(encoding="utf-8"))
    tiles = [tile for layer in layout["levels"].values() for tile in layer]
    graph: dict[str, list[str]] = {}
    for tile in tiles:
        tile_id = str(tile["id"])
        graph[tile_id] = [
            str(upper["id"])
            for upper in tiles
            if int(upper["layer"]) > int(tile["layer"]) and overlaps(tile, upper)
        ]

    cover_counts = Counter(len(blockers) for blockers in graph.values())
    result = {
        "source_level_key": layout["level_key"],
        "tile_count": len(tiles),
        "overlap_range": BLOCK_RANGE,
        "initially_visible": sorted(tile_id for tile_id, blockers in graph.items() if not blockers),
        "blocker_count_histogram": {str(count): total for count, total in sorted(cover_counts.items())},
        "tiles": [
            {
                "id": str(tile["id"]),
                "col": tile["col"],
                "row": tile["row"],
                "layer": tile["layer"],
                "blockers": graph[str(tile["id"])],
            }
            for tile in sorted(tiles, key=lambda item: (int(item["layer"]), str(item["id"])))
        ],
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"tiles={len(tiles)} initial_visible={len(result['initially_visible'])} "
        f"edges={sum(map(len, graph.values()))}"
    )


if __name__ == "__main__":
    main()
