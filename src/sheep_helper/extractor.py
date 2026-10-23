"""Heuristic extraction of board-like tile arrays from decoded JSON payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TYPE_KEYS = ("type", "icon", "tiletype", "cardtype", "itemtype", "skin")
ID_KEYS = ("id", "tileid", "cardid", "uid")
X_KEYS = ("x", "col", "column", "posx", "gridx")
Y_KEYS = ("y", "row", "posy", "gridy")
LAYER_KEYS = ("layer", "z", "level", "depth", "layernum")
SLOT_KEYS = ("slot", "slots", "bucket", "bar", "tray", "selected")


@dataclass(frozen=True)
class Tile:
    id: str
    type: str
    x: float | int | None
    y: float | int | None
    layer: float | int | None


@dataclass(frozen=True)
class Candidate:
    path: str
    score: int
    tiles: list[Tile]
    slot: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "score": self.score,
            "tile_count": len(self.tiles),
            "tile_types": len({tile.type for tile in self.tiles}),
            "slot": self.slot,
            "tiles": [asdict(tile) for tile in self.tiles],
        }


def _lookup(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized = {str(key).lower().replace("_", ""): value for key, value in record.items()}
    for key in keys:
        if key in normalized:
            return normalized[key]
    return None


def _as_tile(record: dict[str, Any], ordinal: int) -> Tile | None:
    tile_type = _lookup(record, TYPE_KEYS)
    if tile_type is None or isinstance(tile_type, (dict, list)):
        return None
    tile_id = _lookup(record, ID_KEYS)
    return Tile(
        id=str(tile_id if tile_id is not None else f"tile-{ordinal}"),
        type=str(tile_type),
        x=_lookup(record, X_KEYS),
        y=_lookup(record, Y_KEYS),
        layer=_lookup(record, LAYER_KEYS),
    )


def _slot_from(container: dict[str, Any]) -> list[str]:
    for key, value in container.items():
        if str(key).lower().replace("_", "") not in SLOT_KEYS or not isinstance(value, list):
            continue
        values: list[str] = []
        for item in value:
            if isinstance(item, dict):
                tile = _as_tile(item, len(values))
                if tile:
                    values.append(tile.type)
            elif isinstance(item, (str, int)):
                values.append(str(item))
        return values
    return []


def _score(tiles: list[Tile]) -> int:
    if len(tiles) < 6:
        return 0
    score = 4 + min(len(tiles) // 6, 8)
    if sum(tile.x is not None for tile in tiles) >= len(tiles) * 0.7:
        score += 3
    if sum(tile.y is not None for tile in tiles) >= len(tiles) * 0.7:
        score += 3
    if sum(tile.layer is not None for tile in tiles) >= len(tiles) * 0.5:
        score += 2
    if len({tile.type for tile in tiles}) >= 2:
        score += 2
    return score


def extract_candidates(payload: Any) -> list[Candidate]:
    """Find arrays of tile records; never mutates the decoded response payload."""
    candidates: list[Candidate] = []

    def walk(node: Any, path: str, parent: dict[str, Any] | None) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}", node)
        elif isinstance(node, list):
            records = [item for item in node if isinstance(item, dict)]
            tiles = [tile for index, item in enumerate(records) if (tile := _as_tile(item, index))]
            score = _score(tiles)
            if score:
                candidates.append(Candidate(path=path, score=score, tiles=tiles, slot=_slot_from(parent or {})))
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", None)

    walk(payload, "$", None)
    return sorted(candidates, key=lambda item: (-item.score, -len(item.tiles), item.path))
