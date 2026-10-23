"""Deterministically restore runtime card types from a four-number map seed."""

from __future__ import annotations


MASK32 = 0xFFFFFFFF


class ClientRandom:
    def __init__(self, seed: list[int]) -> None:
        if (
            len(seed) != 4
            or any(type(value) is not int or not 0 <= value <= MASK32 for value in seed)
            or not any(seed)
        ):
            raise ValueError("地图种子必须是四个 32 位整数且不能全为零")
        self.a, self.b, self.c, self.d = seed

    def random(self) -> float:
        a, b, c, d = self.a, self.b, self.c, self.d
        total = d + b
        high, low = (c + a + (total >> 32)) & MASK32, total & MASK32
        a = (a ^ (((a << 23) & MASK32) | ((b & 0xFFFFFE00) >> 9))) & MASK32
        b = (b ^ ((b << 23) & MASK32)) & MASK32
        r, s = (a ^ c) & MASK32, (b ^ d) & MASK32
        r = (r ^ (a >> 18)) & MASK32
        s = (s ^ ((b >> 18) | ((a & 0x3FFFF) << 14))) & MASK32
        r = (r ^ (c >> 5)) & MASK32
        s = (s ^ ((d >> 5) | ((c & 0x1F) << 27))) & MASK32
        self.a, self.b, self.c, self.d = c, d, r, s
        return 2.3283064365386963e-10 * high + 2.220446049250313e-16 * (low >> 12)

    def shuffle(self, values: list[int]) -> None:
        """Apply the client's in-place Fisher-Yates shuffle."""
        for index in range(len(values) - 1, -1, -1):
            swap = int(self.random() * (index + 1))
            values[index], values[swap] = values[swap], values[index]


def shuffled_type_pool(
    layout: dict[str, object], seed: list[int],
) -> tuple[list[int], ClientRandom]:
    """Build the initial type pool and return its advanced client RNG."""
    rng = ClientRandom(seed)
    rng.random()  # The client consumes one value while resetting the seed.
    counts = layout["block_type_counts"]
    if not isinstance(counts, dict):
        raise ValueError("地图缺少 block_type_counts")
    pool = [
        int(card_type)
        for card_type, group_count in sorted(counts.items(), key=lambda pair: int(pair[0]))
        for _ in range(3 * int(group_count))
    ]
    rng.shuffle(pool)
    return pool, rng


def restore_cards(layout: dict[str, object], seed: list[int]) -> dict[str, object]:
    """Assign the exact card type and visible resource path to every tile."""
    pool, _ = shuffled_type_pool(layout, seed)
    levels = layout["levels"]
    if not isinstance(levels, dict):
        raise ValueError("地图缺少 levels")
    tiles: list[dict[str, object]] = []
    for _layer, cards in sorted(levels.items(), key=lambda pair: int(pair[0])):
        if not isinstance(cards, list):
            raise ValueError("地图层格式错误")
        for card in cards:
            if not isinstance(card, dict):
                raise ValueError("地图牌格式错误")
            restored = dict(card)
            restored["type"] = int(restored.get("type", 0)) or pool.pop()
            restored["face_resource"] = f"block/daily/block_{restored['type']}"
            tiles.append(restored)
    if pool:
        raise ValueError("地图牌池数量与牌阵不一致")
    result = {
        "level_key": layout["level_key"],
        "tile_count": len(tiles),
        "map_seed": list(seed),
        "tiles": tiles,
    }
    if "source_map_md5" in layout:
        result["source_map_md5"] = layout["source_map_md5"]
    return result
