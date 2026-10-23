"""Strict mitmproxy capture for public Sheep Planet map assets.

Unlike the generic JSON inspector, this addon never labels an arbitrary API
array as a board.  A formal board pipeline begins only with a ``<md5>.map``
asset and a matching game-start record.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sheep_helper.redaction import redact
from sheep_helper.capture_state import CAPTURE_DONE_MARKER, CAPTURE_FORMAT_VERSION, is_game_api_path, safe_game_route, valid_map_ids
from sheep_helper.match_data import extract_runtime_cards


MAP_PATH = re.compile(r"/(?P<md5>[0-9a-f]{32})\.map$", re.IGNORECASE)


def _game_host(host: str) -> bool:
    return host == "easygame2021.com" or host.endswith(".easygame2021.com")


def _content(response: Any) -> bytes:
    getter = getattr(response, "get_content", None)
    return bytes(getter(strict=False) if getter is not None else response.content)


class GameAssetCapture:
    def __init__(self, output_dir: str = "captured-layouts") -> None:
        self.output_dir = Path(output_dir)
        self.map_dir = self.output_dir / "maps"
        self.seed_dir = self.output_dir / "seeds"
        self.status = {"capture_format_version": CAPTURE_FORMAT_VERSION,
                       "listener_pid": os.getpid(),
                       "map_assets": 0, "game_starts": 0, "rejected_assets": 0,
                       "api_routes": {}}

    def _write_status(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "game-capture-status.json").write_text(
            json.dumps(self.status, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _save_game_start(self, payload: dict[str, Any], path: str) -> str | None:
        data = payload.get("data")
        if (not isinstance(data, dict) or valid_map_ids(data.get("map_md5")) is None
                or not any(key in data for key in ("map_seed", "map_seed_2"))):
            return None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        map_ids = valid_map_ids(data["map_md5"])
        runtime_file = None
        match_data = data.get("match_data")
        if isinstance(match_data, str) and match_data:
            try:
                runtime_cards = extract_runtime_cards(match_data, map_ids)
                runtime_dir = self.output_dir / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                runtime_file = f"runtime-{int(time.time() * 1000)}.json"
                (runtime_dir / runtime_file).write_text(
                    json.dumps(runtime_cards, ensure_ascii=False), encoding="utf-8"
                )
            except (KeyError, TypeError, ValueError, OSError):
                # Never promote an undecoded server snapshot to exact card faces.
                runtime_file = None
        record = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_path": safe_game_route(path),
            "map_md5": data.get("map_md5"),
            "map_seed": data.get("map_seed"),
            "need_seed": data.get("need_seed"),
            "need_wx_encrypt": data.get("need_wx_encrypt"),
            "match_data_present": bool(match_data),
            "runtime_cards_file": runtime_file,
        }
        (self.output_dir / "latest-game-start.json").write_text(
            json.dumps(redact(record), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Some modes may supply a plain, nonzero seed without the encrypted
        # follow-up request. Only promote a complete seed bound to valid maps.
        seed = data.get("map_seed")
        if (not data.get("need_seed") and map_ids is not None
                and isinstance(seed, list) and len(seed) == 4
                and all(type(value) is int and 0 <= value <= 0xFFFFFFFF for value in seed)
                and any(seed)):
            self.seed_dir.mkdir(parents=True, exist_ok=True)
            output = self.seed_dir / f"recovered-{int(time.time() * 1000)}.json"
            output.write_text(json.dumps({"map_md5": map_ids, "map_seed": seed,
                                          "source_path": safe_game_route(path),
                                          "match_data_present": bool(match_data),
                                          "runtime_cards_file": runtime_file,
                                          "method": "plain-seed-response"},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        self.status["game_starts"] += 1
        return runtime_file

    def _observe_route(self, path: str, status_code: int, payload: Any) -> None:
        # Paths with dynamic IDs are intentionally not retained. Never save
        # bodies, query strings, headers, or field values in diagnostics.
        path = safe_game_route(path)
        if path is None:
            return
        routes = self.status["api_routes"]
        if path not in routes and len(routes) >= 64:
            return
        entry = routes.setdefault(path, {"count": 0, "status": status_code, "data_keys": []})
        entry["count"] += 1
        entry["status"] = status_code
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            entry["data_keys"] = sorted(str(key) for key in data)[:48]

    def response(self, flow: Any) -> None:
        if (self.output_dir / CAPTURE_DONE_MARKER).exists():
            return
        request = flow.request
        response = flow.response
        if not _game_host(request.pretty_host.lower()):
            return
        path = request.path.split("?", 1)[0]
        match = MAP_PATH.search(path)
        if match is not None:
            raw = _content(response)
            if response.status_code == 200 and raw.startswith(b".map"):
                self.map_dir.mkdir(parents=True, exist_ok=True)
                destination = self.map_dir / f"{match.group('md5').lower()}.map"
                destination.write_bytes(raw)
                self.status["map_assets"] += 1
            else:
                self.status["rejected_assets"] += 1
            self._write_status()
            return
        if not is_game_api_path(path):
            return
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            return
        try:
            payload = json.loads(response.get_text(strict=False))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        self._observe_route(path, response.status_code, payload)
        if response.status_code == 200 and isinstance(payload, dict):
            runtime_file = self._save_game_start(payload, path)
            metadata = getattr(flow, "metadata", None)
            if isinstance(metadata, dict):
                if runtime_file is not None:
                    metadata["sheep_runtime_cards_file"] = runtime_file
                data = payload.get("data")
                if isinstance(data, dict):
                    metadata["sheep_match_data_present"] = bool(data.get("match_data"))
        self._write_status()


class CaptureAddon:
    """mitmproxy wrapper; output location is configured with ``--set``."""

    def __init__(self) -> None:
        self.capture = GameAssetCapture()

    def load(self, loader: Any) -> None:
        loader.add_option(
            name="game_capture_dir", typespec=str, default="captured-layouts",
            help="Only public .map assets and redacted game-start metadata are stored.",
        )

    def configure(self, updated: set[str]) -> None:
        if "game_capture_dir" in updated:
            from mitmproxy import ctx
            self.capture = GameAssetCapture(ctx.options.game_capture_dir)
            self.capture._write_status()

    def response(self, flow: Any) -> None:
        self.capture.response(flow)


addons = [CaptureAddon()]
