"""Decode the client's UTF-16 LZ-string match snapshot, retaining only cards.

The game calls ``JsonToFirstGameStateSyncV0`` on ``match_data`` and uses its
``allBlockRuntimeData`` in place of the seed-generated map when it is present.
No account or network fields are written to the extracted snapshot.
"""

from __future__ import annotations

import json
from typing import Any


def _decompress_utf16(value: str, *, max_output: int = 2_000_000) -> str:
    if not value:
        raise ValueError("空的 match_data")
    position = 16384
    index = 1
    current = ord(value[0]) - 32

    def bits(count: int) -> int:
        nonlocal position, index, current
        result = 0
        for shift in range(count):
            if current & position:
                result |= 1 << shift
            position >>= 1
            if position == 0:
                position = 16384
                if index >= len(value):
                    raise ValueError("截断的 match_data")
                current = ord(value[index]) - 32
                index += 1
        return result

    initial = bits(2)
    if initial == 0:
        first = chr(bits(8))
    elif initial == 1:
        first = chr(bits(16))
    else:
        raise ValueError("无效的 match_data 起始码")
    dictionary = ["", "", "", first]
    previous = first
    output = [first]
    output_size = 1
    dictionary_size = 4
    enlarge_in = 4
    num_bits = 3
    while True:
        code = bits(num_bits)
        if code in (0, 1):
            entry = chr(bits(8 if code == 0 else 16))
            dictionary.append(entry)
            code = dictionary_size
            dictionary_size += 1
            enlarge_in -= 1
        elif code == 2:
            return "".join(output)
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
        if code < len(dictionary):
            entry = dictionary[code]
        elif code == dictionary_size:
            entry = previous + previous[0]
        else:
            raise ValueError("无效的 match_data 字典索引")
        output_size += len(entry)
        if output_size > max_output:
            raise ValueError("match_data 解压结果过大")
        output.append(entry)
        dictionary.append(previous + entry[0])
        dictionary_size += 1
        enlarge_in -= 1
        previous = entry
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1


def extract_runtime_cards(value: str, map_ids: list[str]) -> dict[str, list[dict[str, int | str]]]:
    """Return exact server-supplied cards keyed by map MD5, or fail closed."""
    snapshots: Any = json.loads(_decompress_utf16(value))
    if not isinstance(snapshots, list) or len(snapshots) != len(map_ids):
        raise ValueError("match_data 关卡数与地图数不一致")
    result: dict[str, list[dict[str, int | str]]] = {}
    for map_id, snapshot in zip(map_ids, snapshots):
        raw = snapshot["gameState"]["allBlockRuntimeData"]
        if not isinstance(raw, list) or not raw:
            raise ValueError("match_data 缺少运行时牌面")
        cards: list[dict[str, int | str]] = []
        seen: set[str] = set()
        for item in raw:
            layer, col, row, card_type = (item[key] for key in ("layerNum", "colNum", "rowNum", "type"))
            if any(type(number) is not int for number in (layer, col, row, card_type)) or not 1 <= card_type <= 24:
                raise ValueError("match_data 牌坐标或类型无效")
            tile_id = f"{layer}-{col}-{row}"
            if tile_id in seen:
                raise ValueError("match_data 包含重复牌")
            seen.add(tile_id)
            cards.append({"id": tile_id, "type": card_type})
        result[map_id] = cards
    return result
