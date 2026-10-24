"""Validate and assemble captured map, recovered seed, cards and coverage."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .card_types import restore_cards
from .coverage import build_coverage
from .map_format import decode_map


MAP_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def map_id_from_path(path: Path) -> str:
    map_id = path.name.split(".", 1)[0].lower()
    if not MAP_ID.fullmatch(map_id):
        raise ValueError("地图文件名必须以 32 位 map_md5 开头")
    return map_id


def validate_seed_record(record: dict[str, object], map_id: str) -> list[int]:
    mapped = record.get("map_md5")
    map_ids = [mapped] if isinstance(mapped, str) else mapped
    if not isinstance(map_ids, list) or map_id not in [str(item).lower() for item in map_ids]:
        raise ValueError("种子记录的 map_md5 与地图不匹配")
    seed = record.get("map_seed")
    if (
        not isinstance(seed, list)
        or len(seed) != 4
        or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in seed)
        or not any(seed)
    ):
        raise ValueError("地图种子必须是四个 32 位整数且不能全为零")
    return seed


def assemble_session(
    map_path: Path,
    seed_path: Path,
    observed_path: Path | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    map_id = map_id_from_path(map_path)
    record = json.loads(seed_path.read_text(encoding="utf-8"))
    seed = validate_seed_record(record, map_id)
    layout = decode_map(map_path.read_bytes())
    layout["source_map_md5"] = map_id
    cards = restore_cards(layout, seed)
    runtime_file = record.get("runtime_cards_file")
    if record.get("match_data_present") and not runtime_file:
        raise ValueError("服务器提供了运行时牌面，但本次未能解包；不能按种子推算的牌面指引")
    if runtime_file is not None:
        if (not isinstance(runtime_file, str)
                or not re.fullmatch(r"runtime-\d+\.json", runtime_file)):
            raise ValueError("运行时牌面文件名无效")
        runtime_path = seed_path.parent.parent / "runtime" / runtime_file
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        raw_tiles = runtime.get(map_id) if isinstance(runtime, dict) else None
        if not isinstance(raw_tiles, list):
            raise ValueError("运行时牌面缺少目标地图")
        by_id = {item["id"]: item["type"] for item in raw_tiles}
        expected = {tile["id"] for tile in cards["tiles"]}
        if len(by_id) != len(raw_tiles) or set(by_id) != expected:
            raise ValueError("运行时牌面与地图坐标不一致；请重开新局抓包")
        for tile in cards["tiles"]:
            card_type = by_id[tile["id"]]
            if type(card_type) is not int or not 1 <= card_type <= 24:
                raise ValueError("运行时牌型无效")
            tile["type"] = card_type
            tile["face_resource"] = f"block/daily/block_{card_type}"
        cards["verification_mode"] = "server-runtime"
    elif (record.get("match_data_present") is False
          and record.get("method") in {"local-known-plaintext-ofb-probe", "plain-seed-response"}):
        # A fresh game has no server snapshot. The client assigns every face
        # from this seed and the map's type pool; backflow games use match_data.
        cards["verification_mode"] = "client-seed-fresh"
    coverage = build_coverage(layout)
    if len(cards["tiles"]) != len(coverage["tiles"]):
        raise ValueError("牌面数与遮挡图不一致")
    if observed_path is not None:
        observed = json.loads(observed_path.read_text(encoding="utf-8"))
        if not isinstance(observed, dict) or not observed:
            raise ValueError("实测牌面须是非空的 {tile_id: type 或图案名称} 对照表")
        by_id = {str(tile["id"]): int(tile["type"]) for tile in cards["tiles"]}
        unknown = [str(tile_id) for tile_id in observed if str(tile_id) not in by_id]
        if unknown:
            raise ValueError(f"实测包含未知牌：{', '.join(unknown[:5])}")
        if all(type(value) is int for value in observed.values()):
            mismatched = [str(tile_id) for tile_id, value in observed.items() if by_id[str(tile_id)] != value]
            if mismatched:
                raise ValueError(f"实测牌面与恢复结果不符：{', '.join(mismatched[:5])}")
            cards["verification_mode"] = "exact-type"
            cards["verified_visible_ids"] = sorted(str(tile_id) for tile_id in observed)
        elif all(isinstance(value, str) and value.strip() for value in observed.values()):
            label_to_type: dict[str, int] = {}
            type_to_label: dict[int, str] = {}
            for tile_id, label in observed.items():
                card_type = by_id[str(tile_id)]
                if label in label_to_type and label_to_type[label] != card_type:
                    raise ValueError(f"同一可见图案出现不同的恢复类型：{label}")
                if card_type in type_to_label and type_to_label[card_type] != label:
                    raise ValueError(f"同一恢复类型出现不同的可见图案：{card_type}")
                label_to_type[label] = card_type
                type_to_label[card_type] = label
            cards["verification_mode"] = "visual-grouping"
            cards["observed_face_labels"] = {str(key): value for key, value in sorted(type_to_label.items())}
        else:
            raise ValueError("实测值必须全部为整数类型，或全部为非空图案名称")
        cards["verified_visible_count"] = len(observed)
    return layout, coverage, cards


def write_session(
    map_path: Path,
    seed_path: Path,
    output_dir: Path,
    observed_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    layout, coverage, cards = assemble_session(map_path, seed_path, observed_path)
    map_id = layout["source_map_md5"]
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = (
        output_dir / f"{map_id}.layout.json",
        output_dir / f"{map_id}.coverage.json",
        output_dir / f"{map_id}.cards.json",
    )
    serialized = [json.dumps(payload, ensure_ascii=False, indent=2) for payload in (layout, coverage, cards)]
    for index, (path, content) in enumerate(zip(outputs, serialized)):
        if not path.exists():
            continue
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            continue
        if index == 2 and cards.get("verification_mode") == "client-seed-fresh":
            previous = json.loads(existing)
            upgraded = dict(cards)
            upgraded.pop("verification_mode")
            if previous == upgraded:
                continue
        raise FileExistsError(f"已有不同版本的会话文件，拒绝覆盖：{path}")
    for index, (path, content) in enumerate(zip(outputs, serialized)):
        if path.exists():
            if index != 2 or path.read_text(encoding="utf-8") == content:
                continue
            temporary = path.with_suffix(".cards.json.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        else:
            path.write_text(content, encoding="utf-8")
    return outputs
