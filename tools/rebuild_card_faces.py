"""Rebuild the card type at every map coordinate from a decrypted map_seed.

This implements the XorShift and Fisher-Yates code in the unpacked client.
It accepts only the four-number gameplay seed; it neither accepts nor handles
WeChat account encryption keys.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MASK32 = 0xFFFFFFFF


class ClientXorShift:
    """Bit-for-bit port of tools/random/xorshift from the game package."""

    def __init__(self, seed: list[int]) -> None:
        if len(seed) != 4:
            raise ValueError("map_seed 必须是恰好四个整数")
        self.state0_u, self.state0_l, self.state1_u, self.state1_l = (
            value & MASK32 for value in seed
        )

    def random(self) -> float:
        e, t, n, i = self.state0_u, self.state0_l, self.state1_u, self.state1_l
        total = i + t
        high = (n + e + (total >> 32)) & MASK32
        low = total & MASK32
        e = (e ^ (((e << 23) & MASK32) | ((t & 0xFFFFFE00) >> 9))) & MASK32
        t = (t ^ ((t << 23) & MASK32)) & MASK32
        r = (e ^ n) & MASK32
        c = (t ^ i) & MASK32
        r = (r ^ (e >> 18)) & MASK32
        c = (c ^ ((t >> 18) | ((e & 0x3FFFF) << 14))) & MASK32
        r = (r ^ (n >> 5)) & MASK32
        c = (c ^ ((i >> 5) | ((n & 0x1F) << 27))) & MASK32
        self.state0_u, self.state0_l = n, i
        self.state1_u, self.state1_l = r, c
        return 2.3283064365386963e-10 * high + 2.220446049250313e-16 * (low >> 12)


def shuffle(items: list[int], rng: ClientXorShift) -> list[int]:
    for index in range(len(items) - 1, -1, -1):
        swap_index = int(rng.random() * (index + 1))
        items[index], items[swap_index] = items[swap_index], items[index]
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("layout", type=Path)
    parser.add_argument("--seed", required=True, help="JSON array，例如 [1,2,3,4]")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    seed = json.loads(args.seed)
    if not isinstance(seed, list) or not all(isinstance(value, int) for value in seed):
        raise ValueError("--seed 必须为四个整数的 JSON 数组")
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    rng = ClientXorShift(seed)
    rng.random()
    pool = [
        int(card_type)
        for card_type, card_count in sorted(
            layout["block_type_counts"].items(), key=lambda item: int(item[0])
        )
        for _ in range(3 * int(card_count))
    ]
    shuffle(pool, rng)

    # Numeric JavaScript object keys enumerate layers ascending; each protobuf
    # list retains its encoded order. SetBlockCardType consumes the pool by pop.
    tiles = [
        tile
        for _layer, tile_list in sorted(
            layout["levels"].items(), key=lambda item: int(item[0])
        )
        for tile in tile_list
    ]
    if len(pool) != len(tiles):
        raise ValueError(f"牌池 {len(pool)} 张与地图 {len(tiles)} 张不一致")
    for card_id, tile in enumerate(tiles):
        tile["card_id"] = card_id
        tile["type"] = pool.pop()
        # The runtime loads the visible face with this exact resource path.
        tile["face_resource"] = f"block/daily/block_{tile['type']}"
    result = {
        "level_key": layout["level_key"],
        "tile_count": len(tiles),
        "map_seed": seed,
        "tiles": tiles,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tiles={len(tiles)} types={len(set(tile['type'] for tile in tiles))}")


if __name__ == "__main__":
    main()
