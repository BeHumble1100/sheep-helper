"""Build the client-equivalent covering graph for a decoded map."""

from __future__ import annotations

from collections import Counter


BLOCK_RANGE = 8


def build_coverage(layout: dict[str, object]) -> dict[str, object]:
    levels = layout.get("levels")
    if not isinstance(levels, dict):
        raise ValueError("地图缺少 levels")
    tiles = [tile for cards in levels.values() for tile in cards]
    ids = [str(tile["id"]) for tile in tiles]
    if len(set(ids)) != len(ids):
        raise ValueError("地图中存在重复 tile id")
    if len(tiles) != layout.get("tile_count"):
        raise ValueError("地图牌数与 tile_count 不一致")
    graph = {
        str(tile["id"]): [
            str(upper["id"])
            for upper in tiles
            if int(upper["layer"]) > int(tile["layer"])
            and abs(int(upper["col"]) - int(tile["col"])) < BLOCK_RANGE
            and abs(int(upper["row"]) - int(tile["row"])) < BLOCK_RANGE
        ]
        for tile in tiles
    }
    counts = Counter(map(len, graph.values()))
    result = {
        "source_level_key": layout["level_key"],
        "tile_count": len(tiles),
        "overlap_range": BLOCK_RANGE,
        "initially_visible": sorted(tile_id for tile_id, blockers in graph.items() if not blockers),
        "blocker_count_histogram": {str(count): total for count, total in sorted(counts.items())},
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
    if "source_map_md5" in layout:
        result["source_map_md5"] = layout["source_map_md5"]
    return result
