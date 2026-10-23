"""Player-facing names in the game's numbered card-type order."""

from __future__ import annotations


# Confirmed from the player's complete, row-major 4 × 4 face selection screen.
FACE_NAMES = {
    1: "草丛",
    2: "萝卜",
    3: "玉米",
    4: "木桩",
    5: "草叉",
    6: "白菜",
    7: "毛球",
    8: "刷子",
    9: "剪刀",
    10: "奶瓶",
    11: "水桶",
    12: "手套",
    13: "铃铛",
    14: "火堆",
    15: "毛线",
    16: "稻草",
}


def face_name(card_type: int) -> str:
    return FACE_NAMES.get(card_type, f"牌型 {card_type}（图案待核对）")


def board_region(col: int, row: int, *, min_col: int, max_col: int,
                 min_row: int, max_row: int) -> str:
    """Give a coarse landmark; the overlay remains the precise locator."""
    x = (col - min_col) / max(1, max_col - min_col)
    y = (row - min_row) / max(1, max_row - min_row)
    horizontal = "左" if x < 1 / 3 else "右" if x > 2 / 3 else "中"
    vertical = "上" if y < 1 / 3 else "下" if y > 2 / 3 else "中"
    if horizontal == vertical == "中":
        return "中央区域"
    if horizontal == "中":
        return f"{vertical}方中央"
    if vertical == "中":
        return f"{horizontal}侧中央"
    return f"{horizontal}{vertical}区域"
