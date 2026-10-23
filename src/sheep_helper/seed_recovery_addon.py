"""Optional, local-only recovery of an encrypted map seed.

This addon is intentionally separate from normal asset capture.  With explicit
user opt-in it substitutes a known seed token in one *local* game-start
response, derives the OFB keystream from the next request, restores the
original ciphertext before forwarding, and saves only the resulting four
integers.  It never writes request headers, cookies, tokens, or the stream.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from sheep_helper.capture_state import CAPTURE_DONE_MARKER, is_game_api_path, is_seed_path, safe_game_route, valid_map_ids


KNOWN_TOKEN = "A" * 96


def _read_varint(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(raw) and shift <= 70:
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 128:
            return value, offset
        shift += 7
    raise ValueError("无效 protobuf varint")


def _seed_message(token: str) -> bytes:
    encoded = token.encode("utf-8")
    if len(encoded) >= 128:
        raise ValueError("种子令牌过长")
    return bytes((10, len(encoded))) + encoded


def _decode_seed(raw: bytes) -> list[int]:
    offset = 0
    code: int | None = None
    values: list[int] = []
    while offset < len(raw):
        tag, offset = _read_varint(raw, offset)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, offset = _read_varint(raw, offset)
            if number == 1:
                code = value
            elif number == 2:
                values.append(value)
        elif wire == 2:
            size, offset = _read_varint(raw, offset)
            packed = raw[offset:offset + size]
            if len(packed) != size:
                raise ValueError("截断的种子响应")
            offset += size
            if number == 2:
                cursor = 0
                while cursor < len(packed):
                    value, cursor = _read_varint(packed, cursor)
                    values.append(value)
        else:
            raise ValueError("不支持的种子 protobuf 字段")
    if code != 1 or len(values) != 4:
        raise ValueError("种子响应不是四整数成功结果")
    return values


def _request_info(flow: Any) -> tuple[str, tuple[str, Any]]:
    body = flow.request.get_text()
    if "application/x-www-form-urlencoded" in flow.request.headers.get("content-type", "").lower():
        pairs = parse_qsl(body, keep_blank_values=True)
        for key, value in pairs:
            if key == "info":
                return value, ("form", pairs)
        raise ValueError("种子请求缺少 info")
    payload = json.loads(body)
    return str(payload["info"]), ("json", payload)


def _replace_info(flow: Any, saved: tuple[str, Any], value: str) -> None:
    kind, data = saved
    if kind == "form":
        flow.request.set_text(urlencode([(key, value if key == "info" else item) for key, item in data]))
    else:
        payload = dict(data)
        payload["info"] = value
        flow.request.set_text(json.dumps(payload, separators=(",", ":")))


class SeedRecovery:
    def __init__(self, output_dir: str = "captured-layouts") -> None:
        self.output_dir = Path(output_dir)
        self.pending: tuple[str, object, str | None, str | None, bool] | None = None

    @staticmethod
    def _game_host(flow: Any) -> bool:
        host = flow.request.pretty_host.lower()
        return host == "easygame2021.com" or host.endswith(".easygame2021.com")

    def response(self, flow: Any) -> None:
        if (self.output_dir / CAPTURE_DONE_MARKER).exists():
            self.pending = None
            return
        if (self._game_host(flow) and is_game_api_path(flow.request.path)
                and not is_seed_path(flow.request.path)
                and flow.response.status_code == 200):
            try:
                payload = json.loads(flow.response.get_text())
                data = payload["data"]
                if not isinstance(data, dict):
                    return
                token = data.get("map_seed_2")
                if (data.get("need_seed") and valid_map_ids(data.get("map_md5")) is not None
                        and isinstance(token, str) and token):
                    self.pending = (
                        token, data["map_md5"], safe_game_route(flow.request.path),
                        flow.metadata.get("sheep_runtime_cards_file"),
                        bool(data.get("match_data")),
                    )
                    data["map_seed_2"] = KNOWN_TOKEN
                    flow.response.set_text(json.dumps(payload, separators=(",", ":")))
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self.pending = None
            return
        if not self._game_host(flow) or not is_seed_path(flow.request.path) or "seed_recovery_stream" not in flow.metadata:
            return
        try:
            stream = flow.metadata.pop("seed_recovery_stream")
            raw = bytes(flow.response.get_content(strict=False) or b"")
            if len(raw) > len(stream):
                raise ValueError("种子响应长度超出已知流")
            seed = _decode_seed(bytes(left ^ right for left, right in zip(raw, stream)))
            self.output_dir.joinpath("seeds").mkdir(parents=True, exist_ok=True)
            output = self.output_dir / "seeds" / f"recovered-{int(time.time() * 1000)}.json"
            output.write_text(json.dumps({
                "map_md5": flow.metadata.pop("seed_recovery_maps", None),
                "map_seed": seed,
                "source_path": flow.metadata.pop("seed_recovery_source", None),
                "runtime_cards_file": flow.metadata.pop("seed_recovery_runtime", None),
                "match_data_present": flow.metadata.pop("seed_recovery_match_data", False),
                "method": "local-known-plaintext-ofb-probe",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except (TypeError, ValueError, OSError):
            # The game response remains untouched; an unsuccessful probe is not
            # silently promoted into a usable seed file.
            pass
        finally:
            flow.metadata.pop("seed_recovery_maps", None)
            flow.metadata.pop("seed_recovery_source", None)
            flow.metadata.pop("seed_recovery_runtime", None)
            flow.metadata.pop("seed_recovery_match_data", None)

    def request(self, flow: Any) -> None:
        if (self.output_dir / CAPTURE_DONE_MARKER).exists():
            self.pending = None
            return
        if self.pending is None or not self._game_host(flow) or not is_seed_path(flow.request.path):
            return
        token, map_md5, source_path, runtime_file, match_data_present = self.pending
        self.pending = None
        try:
            encoded, saved = _request_info(flow)
            framed = base64.b64decode(encoded, validate=True)
            known = _seed_message(KNOWN_TOKEN)
            if len(framed) != len(known) + 3 or framed[0] != 1:
                raise ValueError("未识别的加密种子请求帧")
            stream = bytes(left ^ right for left, right in zip(framed[3:], known))
            original = _seed_message(token)
            restored = framed[:3] + bytes(left ^ right for left, right in zip(original, stream))
            _replace_info(flow, saved, base64.b64encode(restored).decode("ascii"))
            flow.metadata["seed_recovery_stream"] = stream
            flow.metadata["seed_recovery_maps"] = map_md5
            flow.metadata["seed_recovery_source"] = source_path
            flow.metadata["seed_recovery_runtime"] = runtime_file
            flow.metadata["seed_recovery_match_data"] = match_data_present
        except Exception:
            # A failed probe must leave no retained seed material; the ordinary
            # request continues unchanged whenever replacement was not reached.
            self.pending = None


class SeedRecoveryAddon:
    def __init__(self) -> None:
        self.recovery = SeedRecovery()

    def load(self, loader: Any) -> None:
        loader.add_option(
            name="seed_recovery_dir", typespec=str, default="captured-layouts",
            help="Directory for local-only recovered four-integer map seeds.",
        )

    def configure(self, updated: set[str]) -> None:
        if "seed_recovery_dir" in updated:
            from mitmproxy import ctx
            self.recovery = SeedRecovery(ctx.options.seed_recovery_dir)

    def response(self, flow: Any) -> None:
        self.recovery.response(flow)

    def request(self, flow: Any) -> None:
        self.recovery.request(flow)


addons = [SeedRecoveryAddon()]
