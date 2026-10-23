"""Decode the public GameMap protobuf payload carried by a ``.map`` asset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterator


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise ValueError("无效 protobuf varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    pos = 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = read_varint(data, pos)
        elif wire == 2:
            length, pos = read_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
        elif wire == 5:
            value = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
        elif wire == 1:
            value = int.from_bytes(data[pos : pos + 8], "little")
            pos += 8
        else:
            raise ValueError(f"不支持的 protobuf wire type: {wire}")
        yield number, wire, value


def int_field(data: bytes, wanted: int, default: int = 0) -> int:
    for number, wire, value in fields(data):
        if number == wanted and wire == 0:
            return int(value)
    return default


def decode_node(data: bytes) -> dict[str, int | str]:
    node: dict[str, int | str] = {
        "id": "",
        "type": 0,
        "col": 0,
        "row": 0,
        "layer": 0,
        "mold_type": 0,
        "meta_type": 0,
        "meta_data": 0,
    }
    names = {2: "type", 3: "col", 4: "row", 5: "layer", 6: "mold_type", 7: "meta_type", 8: "meta_data"}
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            node["id"] = bytes(value).decode("utf-8", errors="replace")
        elif number in names and wire == 0:
            node[names[number]] = int(value)
    return node


def decode_nodes(data: bytes) -> list[dict[str, int | str]]:
    return [decode_node(bytes(value)) for number, wire, value in fields(data) if number == 1 and wire == 2]


def decode_map_entry(data: bytes) -> tuple[str, list[dict[str, int | str]]]:
    key = ""
    nodes: list[dict[str, int | str]] = []
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            key = bytes(value).decode("utf-8", errors="replace")
        elif number == 2 and wire == 2:
            nodes = decode_nodes(bytes(value))
    return key, nodes


def decode_count_map_entry(data: bytes) -> tuple[str, int]:
    key = ""
    count = 0
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            key = bytes(value).decode("utf-8", errors="replace")
        elif number == 2 and wire == 0:
            count = int(value)
    return key, count


def decode_game_map(raw: bytes) -> dict[str, object]:
    # The game client deliberately skips a 21-byte binary asset wrapper.
    data = raw[21:]
    result: dict[str, object] = {"width": 0, "height": 0, "level_key": 0, "block_type_counts": {}, "levels": {}}
    counts: dict[str, int] = {}
    levels: dict[str, list[dict[str, int | str]]] = {}
    for number, wire, value in fields(data):
        if number == 1 and wire == 0:
            result["width"] = int(value)
        elif number == 2 and wire == 0:
            result["height"] = int(value)
        elif number == 3 and wire == 0:
            result["level_key"] = int(value)
        elif number == 4 and wire == 2:
            key, count = decode_count_map_entry(bytes(value))
            counts[key] = count
        elif number == 5 and wire == 2:
            key, nodes = decode_map_entry(bytes(value))
            levels[key] = nodes
    result["block_type_counts"] = counts
    result["levels"] = levels
    result["tile_count"] = sum(len(nodes) for nodes in levels.values())
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    decoded = decode_game_map(args.source.read_bytes())
    args.output.write_text(json.dumps(decoded, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tiles={decoded['tile_count']} width={decoded['width']} height={decoded['height']} level_key={decoded['level_key']}")


if __name__ == "__main__":
    main()
