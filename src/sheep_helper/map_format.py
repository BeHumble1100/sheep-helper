"""Decoder for the public binary ``.map`` layout asset.

The asset contains geometry and type-group counts only.  Runtime card faces
are assigned later from the map seed, so this module intentionally does not
attempt to guess them.
"""

from __future__ import annotations

from collections.abc import Iterator


ASSET_HEADER_BYTES = 21


def _varint(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(raw) and shift <= 63:
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("无效或截断的 protobuf varint")


def _fields(raw: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    offset = 0
    while offset < len(raw):
        tag, offset = _varint(raw, offset)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, offset = _varint(raw, offset)
        elif wire == 2:
            size, offset = _varint(raw, offset)
            value = raw[offset:offset + size]
            if len(value) != size:
                raise ValueError("截断的 protobuf 字段")
            offset += size
        elif wire == 5:
            if offset + 4 > len(raw):
                raise ValueError("截断的 fixed32 字段")
            value = int.from_bytes(raw[offset:offset + 4], "little")
            offset += 4
        elif wire == 1:
            if offset + 8 > len(raw):
                raise ValueError("截断的 fixed64 字段")
            value = int.from_bytes(raw[offset:offset + 8], "little")
            offset += 8
        else:
            raise ValueError(f"不支持的 protobuf wire type: {wire}")
        yield number, wire, value


def _node(raw: bytes) -> dict[str, int | str]:
    fields: dict[str, int | str] = {
        "id": "", "type": 0, "col": 0, "row": 0, "layer": 0,
        "mold_type": 0, "meta_type": 0, "meta_data": 0,
    }
    names = {2: "type", 3: "col", 4: "row", 5: "layer", 6: "mold_type", 7: "meta_type", 8: "meta_data"}
    for number, wire, value in _fields(raw):
        if number == 1 and wire == 2:
            fields["id"] = bytes(value).decode("utf-8", errors="replace")
        elif number in names and wire == 0:
            fields[names[number]] = int(value)
    return fields


def _map_entry(raw: bytes) -> tuple[str, list[dict[str, int | str]]]:
    key = ""
    cards: list[dict[str, int | str]] = []
    for number, wire, value in _fields(raw):
        if number == 1 and wire == 2:
            key = bytes(value).decode("utf-8", errors="replace")
        elif number == 2 and wire == 2:
            cards = [_node(bytes(node)) for field, kind, node in _fields(bytes(value)) if field == 1 and kind == 2]
    return key, cards


def _count_entry(raw: bytes) -> tuple[str, int]:
    key = ""
    count = 0
    for number, wire, value in _fields(raw):
        if number == 1 and wire == 2:
            key = bytes(value).decode("utf-8", errors="replace")
        elif number == 2 and wire == 0:
            count = int(value)
    return key, count


def decode_map(raw: bytes) -> dict[str, object]:
    """Return the stable, serializable layout carried in a map asset."""
    if len(raw) <= ASSET_HEADER_BYTES or not raw.startswith(b".map"):
        raise ValueError("不是预期的 .map 资源")
    layout: dict[str, object] = {
        "width": 0, "height": 0, "level_key": 0,
        "block_type_counts": {}, "levels": {},
    }
    counts: dict[str, int] = {}
    levels: dict[str, list[dict[str, int | str]]] = {}
    for number, wire, value in _fields(raw[ASSET_HEADER_BYTES:]):
        if wire == 0 and number == 1:
            layout["width"] = int(value)
        elif wire == 0 and number == 2:
            layout["height"] = int(value)
        elif wire == 0 and number == 3:
            layout["level_key"] = int(value)
        elif wire == 2 and number == 4:
            key, count = _count_entry(bytes(value))
            counts[key] = count
        elif wire == 2 and number == 5:
            key, cards = _map_entry(bytes(value))
            levels[key] = cards
    layout["block_type_counts"] = counts
    layout["levels"] = levels
    layout["tile_count"] = sum(len(cards) for cards in levels.values())
    return layout
