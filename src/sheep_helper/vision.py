"""Conservative image-based alignment and click-change checks for the guide."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from statistics import median

from PIL import Image, ImageChops, ImageFilter, ImageStat


@dataclass(frozen=True)
class Alignment:
    center_x: float
    center_y: float
    cell_px: float
    confidence: float


def _spread_points(points: list[tuple[int, int]], limit: int = 30) -> list[tuple[int, int]]:
    unique = sorted(set(points), key=lambda point: (point[1], point[0]))
    if len(unique) <= limit:
        return unique
    return [unique[round(index * (len(unique) - 1) / (limit - 1))] for index in range(limit)]


def find_alignment(
    image: Image.Image, points: list[tuple[int, int]], *, mid_col: float, mid_row: float,
) -> Alignment | None:
    """Fit the tile grid to visible card borders; reject ambiguous screenshots."""
    points = _spread_points(points)
    width, height = image.size
    if len(points) < 8 or width < 250 or height < 300:
        return None
    offsets = [((col - mid_col) / 8, (row - mid_row) / 8) for col, row in points]
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(5))
    pixels = edges.load()
    colors = image.convert("RGB").load()

    def score(cx: int, cy: int, cell: int) -> float:
        radius = round(cell * 0.42)
        corner = round(cell * 0.26)
        inside = 0
        card_faces = 0
        for dx, dy in offsets:
            x, y = round(cx + dx * cell), round(cy + dy * cell)
            if x - radius < 2 or x + radius >= width - 2 or y - radius < 2 or y + radius >= height - 2:
                continue
            inside += 1
            pale_corners = 0
            for side_x in (-1, 1):
                for side_y in (-1, 1):
                    red, _green, blue = colors[x + side_x * corner, y + side_y * corner]
                    pale_corners += red >= 210 and blue >= 160
            card_faces += pale_corners >= 3
        # A misplaced fit can still follow the repeated card grid. Require
        # agreement across most of the actual visible-card coordinates.
        if inside < 8 or card_faces < max(7, round(inside * 0.75)):
            return 0.0
        total = 0
        count = 0
        for dx, dy in offsets:
            x, y = round(cx + dx * cell), round(cy + dy * cell)
            if x - radius < 2 or x + radius >= width - 2 or y - radius < 2 or y + radius >= height - 2:
                continue
            for fraction in (-0.22, 0.0, 0.22):
                shift = round(cell * fraction)
                total += pixels[x - radius, y + shift] + pixels[x + radius, y + shift]
                total += pixels[x + shift, y - radius] + pixels[x + shift, y + radius]
                count += 4
        return (total / count) * (card_faces / inside) ** 2 if count >= 48 else 0.0

    cell_step = max(2, round(width / 160))
    xy_step = max(5, round(width / 75))
    # The dense second-level layout uses cards narrower than one tenth of the
    # client width (about 52 px in a 565 px window).
    cells = range(round(width / 12), round(width / 6) + 1, cell_step)
    # The board is horizontally centered in the game client. Wide searches
    # can lock onto unrelated UI edges, especially on the sparse tutorial map.
    centers_x = range(round(width * 0.32), round(width * 0.68) + 1, xy_step)
    centers_y = range(round(height * 0.18), round(height * 0.70) + 1, xy_step)
    measured: list[float] = []
    best = (0.0, 0, 0, 0)
    for cell in cells:
        for cy in centers_y:
            for cx in centers_x:
                value = score(cx, cy, cell)
                measured.append(value)
                if value > best[0]:
                    best = (value, cx, cy, cell)
    if not measured:
        return None
    _, coarse_x, coarse_y, coarse_cell = best
    for cell in range(max(1, coarse_cell - cell_step), coarse_cell + cell_step + 1):
        for cy in range(coarse_y - xy_step, coarse_y + xy_step + 1):
            for cx in range(coarse_x - xy_step, coarse_x + xy_step + 1):
                value = score(cx, cy, cell)
                if value > best[0]:
                    best = (value, cx, cy, cell)
    baseline = max(1.0, median(measured))
    confidence = best[0] / baseline
    if best[0] < 45 or confidence < 1.45:
        return None
    return Alignment(float(best[1]), float(best[2]), float(best[3]), confidence)


def find_daily_alignment(
    image: Image.Image, points: list[tuple[int, int]], *, mid_col: float, mid_row: float,
) -> Alignment | None:
    """Register the map against exposed card faces anywhere on a daily board.

    Dark stacks and covered cards are outliers; no row, tile count, or day's
    layout is special-cased. If several placements explain the image equally
    well, return no alignment rather than pointing at a guessed tile.
    """
    width, height = image.size
    points = sorted(set(points))
    if width < 250 or height < 300 or len(points) < 4:
        return None
    offsets = [((col - mid_col) / 8, (row - mid_row) / 8) for col, row in points]
    pixels = image.convert("RGB").load()
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(5)).load()
    required = max(4, ceil(len(points) * 0.28))

    def pale(x: int, y: int) -> bool:
        red, _green, blue = pixels[x, y]
        return red >= 210 and blue >= 160

    def score(cx: int, cy: int, cell: int) -> tuple[int, float]:
        radius = round(cell * 0.45)
        corners = tuple(round(cell * fraction) for fraction in (0.31, 0.36, 0.40))
        matched = 0
        strength = 0
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for dx, dy in offsets:
            x, y = round(cx + dx * cell), round(cy + dy * cell)
            if (x - radius < 2 or x + radius >= width - 2 or
                    y - radius < 2 or y + radius >= height - 2):
                continue
            if not all(any(pale(x + side * corner, y - corner) for corner in corners)
                       for side in (-1, 1)):
                continue
            red, green_channel, blue = pixels[x, y]
            icon = (max(red, green_channel, blue) > 140
                    and max(red, green_channel, blue) - min(red, green_channel, blue) > 45
                    and not (green_channel > red * 1.1 and green_channel > blue * 1.1))
            if not (pale(x, y) or icon):
                continue
            top_edge, bottom_edge = edges[x, y - radius], edges[x, y + radius]
            if min(top_edge, bottom_edge) < 40:
                continue
            border = top_edge + bottom_edge
            matched += 1
            strength += border
            min_x, max_x = min(min_x, dx), max(max_x, dx)
            min_y, max_y = min(min_y, dy), max(max_y, dy)
        if (matched < required or max_x - min_x < 2 or max_y - min_y < 2):
            return 0, 0.0
        return matched, strength / matched

    coarse_step = 2 if len(points) <= 6 else max(5, round(width / 75))
    cell_step = 1 if len(points) <= 6 else max(2, round(width / 190))
    candidates: list[tuple[int, float, int, int, int]] = []
    for cell in range(round(width / 13), round(width / 8) + 1, cell_step):
        for cy in range(round(height * 0.25), round(height * 0.65) + 1, coarse_step):
            for cx in range(round(width * 0.38), round(width * 0.62) + 1, coarse_step):
                matched, strength = score(cx, cy, cell)
                if matched:
                    candidates.append((matched, strength, cx, cy, cell))
    if not candidates:
        return None
    rank = lambda item: (item[0], item[1], -abs(item[2] - width / 2))
    candidates.sort(key=rank, reverse=True)
    refined: list[tuple[int, float, int, int, int]] = candidates[:12].copy()
    for _matched, _strength, coarse_x, coarse_y, coarse_cell in candidates[:12]:
        for cell in range(max(1, coarse_cell - 2), coarse_cell + 3):
            for cy in range(coarse_y - coarse_step, coarse_y + coarse_step + 1, 2):
                for cx in range(coarse_x - coarse_step, coarse_x + coarse_step + 1, 2):
                    matched, strength = score(cx, cy, cell)
                    if matched:
                        refined.append((matched, strength, cx, cy, cell))
    refined.sort(key=rank, reverse=True)
    matched, strength, cx, cy, cell = refined[0]
    # A different grid placement with virtually the same support is unsafe.
    rivals = [item for item in refined if (abs(item[2] - cx) > cell * 0.35 or
                                           abs(item[3] - cy) > cell * 0.35 or
                                           abs(item[4] - cell) > cell * 0.12)]
    if rivals and rivals[0][0] >= matched - 1 and rivals[0][1] >= strength * 0.92:
        return None
    return Alignment(float(cx), float(cy), float(cell), matched / len(points))


def is_exposed_card(image: Image.Image, x: float, y: float, cell: float) -> bool:
    """Require the proposed next click to land on a bright card, not grass."""
    width, height = image.size
    cx, cy = round(x), round(y)
    corner = round(cell * 0.30)
    border = round(cell * 0.45)
    if (cx - border < 2 or cy - border < 2 or
            cx + border >= width - 2 or cy + border >= height - 2):
        return False
    pixels = image.convert("RGB").load()
    pale = 0
    for dx in (-corner, corner):
        for dy in (-corner, corner):
            red, _green, blue = pixels[cx + dx, cy + dy]
            pale += red >= 210 and blue >= 160
    if pale < 3:
        return False
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(5)).load()
    return edges[cx, cy - border] >= 35 and edges[cx, cy + border] >= 35


def find_exposed_card_center(
    image: Image.Image, x: float, y: float, cell: float,
) -> tuple[float, float] | None:
    """Snap a nearly aligned target to its visible card border.

    The search stays well inside half a grid pitch, so a nearby staggered or
    adjacent card cannot replace the planned tile.  This corrects the small
    per-frame offset left by global daily-board registration without changing
    the route or its map coordinates.
    """
    width, height = image.size
    if cell < 8 or width < cell or height < cell:
        return None
    border = round(cell * 0.45)
    corner = round(cell * 0.30)
    limit = max(2, round(cell * 0.18))
    colors = image.convert("RGB").load()
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(5)).load()

    def pale(px: int, py: int) -> bool:
        red, _green, blue = colors[px, py]
        return red >= 210 and blue >= 160

    candidates: list[tuple[int, int, int]] = []
    origin_x, origin_y = round(x), round(y)
    edge_offsets = (-round(cell * 0.20), 0, round(cell * 0.20))
    for cy in range(origin_y - limit, origin_y + limit + 1):
        for cx in range(origin_x - limit, origin_x + limit + 1):
            if (cx - border < 2 or cy - border < 2
                    or cx + border >= width - 2 or cy + border >= height - 2):
                continue
            pale_corners = sum(
                pale(cx + dx, cy + dy)
                for dx in (-corner, corner) for dy in (-corner, corner)
            )
            if pale_corners < 3:
                continue
            strength = 0
            for offset in edge_offsets:
                strength += edges[cx + offset, cy - border] + edges[cx + offset, cy + border]
                strength += edges[cx - border, cy + offset] + edges[cx + border, cy + offset]
            candidates.append((strength, cx, cy))
    if not candidates:
        return None
    strongest = max(item[0] for item in candidates)
    if strongest < 420:
        return None
    # FIND_EDGES followed by MaxFilter deliberately thickens the border, so
    # several neighboring centers can tie. Their median is the geometric
    # center; choosing the first or the point nearest the original estimate
    # would retain a systematic offset.
    plateau = [item for item in candidates if item[0] >= strongest * 0.98]
    cx = round(median(item[1] for item in plateau))
    cy = round(median(item[2] for item in plateau))
    return (float(cx), float(cy)) if is_exposed_card(image, cx, cy, cell) else None


def visibly_changed(before: Image.Image, after: Image.Image, minimum: float = 12.0) -> bool:
    if before.size != after.size:
        return False
    difference = ImageChops.difference(before.convert("L"), after.convert("L"))
    return ImageStat.Stat(difference).mean[0] >= minimum


def find_parked_cards(
    image: Image.Image, cell: float, occupied: tuple[bool, bool, bool],
) -> dict[int, tuple[float, float, float]]:
    """Locate the smaller three-column move-out area; reject ambiguous rows.

    The move-out and board containers can have different scales. Anchor the
    search immediately above the wide green tray, independently of board pitch.
    Empty columns retain their original index.
    """
    if cell < 16 or not any(occupied):
        return {}
    scale = 2
    width, height = image.size
    if width < 100 or height < 200:
        return {}
    rgb = image.convert("RGB")
    colors = rgb.load()
    bands: list[list[int]] = []
    for y in range(round(height * 0.60), round(height * 0.93)):
        green = 0
        for fraction in (0.045, 0.05, 0.95, 0.955):
            red, channel, blue = colors[round(width * fraction), y]
            green += red < 180 and channel > red * 1.25 and channel > blue * 1.5
        if green >= 3:
            if not bands or y != bands[-1][-1] + 1:
                bands.append([])
            bands[-1].append(y)
    bands = [band for band in bands if height * 0.045 <= len(band) <= height * 0.18]
    if len(bands) != 1:
        return {}
    tray_top = bands[0][0]
    pitch = width / 9
    top = max(round(height * 0.48), round(tray_top - pitch * 2.2))
    bottom = round(tray_top - pitch * 0.12)
    small = rgb.crop((0, top, width, bottom))
    small = small.resize((max(1, width // scale), max(1, (bottom - top) // scale)))
    pixels = small.load()
    sw, sh = small.size
    # Adjacent pale faces can touch after scaling/downsampling. Segment each
    # fixed column independently so two cards never become one wide component.
    columns = {x: round((x * scale - width / 2) / pitch) + 1 for x in range(sw)}
    mask = {(x, y) for y in range(sh) for x in range(sw)
            if 0 <= columns[x] < 3 and occupied[columns[x]]
            and pixels[x, y][0] >= 210 and pixels[x, y][2] >= 160}
    candidates: dict[int, list[tuple[float, float, float]]] = {slot: [] for slot in range(3)}
    while mask:
        seed = mask.pop()
        slot = columns[seed[0]]
        pending = [seed]
        xs, ys = [], []
        while pending:
            x, y = pending.pop()
            xs.append(x)
            ys.append(y)
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in mask and columns[point[0]] == slot:
                    mask.remove(point)
                    pending.append(point)
        left, right = min(xs) * scale, (max(xs) + 1) * scale
        upper, lower = min(ys) * scale + top, (max(ys) + 1) * scale + top
        side = right - left
        if (pitch * 0.70 <= side <= pitch * 1.20
                and side * 0.88 <= lower - upper <= side * 1.3
                and len(xs) * scale * scale >= side * side * 0.35):
            center_x = (left + right) / 2
            expected_x = width / 2 + (slot - 1) * pitch
            if abs(center_x - expected_x) <= pitch * 0.24:
                candidates[slot].append((center_x, upper + side / 2, side / 0.9))
    slots = [i for i, present in enumerate(occupied) if present]
    from itertools import product
    fits = []
    for group in product(*(candidates[slot] for slot in slots)):
        first = group[0]
        if any(abs(point[1] - first[1]) > pitch * 0.22
               or abs(point[2] - first[2]) > pitch * 0.16 for point in group):
            continue
        if any(abs((point[0] - first[0]) - (slot - slots[0]) * pitch) > pitch * 0.22
               for slot, point in zip(slots, group)):
            continue
        fits.append(dict(zip(slots, group)))
    return fits[0] if len(fits) == 1 else {}
