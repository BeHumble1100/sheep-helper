"""Small shared flag between the desktop helper and capture addons."""

import re

CAPTURE_DONE_MARKER = ".capture-complete"
CAPTURE_FORMAT_VERSION = 2
SAFE_GAME_ROUTE = re.compile(r"^/sheep/v1/game/(?:[a-z_]+/)*[a-z_]+$")
MAP_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def is_game_api_path(path: str) -> bool:
    """Only inspect Sheep Planet game API responses, never unrelated traffic."""
    return path.split("?", 1)[0].startswith("/sheep/v1/game/")


def is_seed_path(path: str) -> bool:
    clean = path.split("?", 1)[0]
    return is_game_api_path(clean) and clean.endswith("/map_info_ex_seed")


def safe_game_route(path: str) -> str | None:
    clean = path.split("?", 1)[0]
    return clean if SAFE_GAME_ROUTE.fullmatch(clean) else None


def valid_map_ids(value: object) -> list[str] | None:
    items = [value] if isinstance(value, str) else value
    if (not isinstance(items, list) or not items
            or not all(isinstance(item, str) and MAP_ID.fullmatch(item) for item in items)):
        return None
    return [item.lower() for item in items]
