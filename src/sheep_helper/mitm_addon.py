"""Optional mitmproxy addon: discard flows and persist only normalized layouts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sheep_helper.extractor import extract_candidates
from sheep_helper.redaction import redact, strip_query


class LayoutOnlyCapture:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.status = {
            "responses_seen": 0,
            "json_responses_seen": 0,
            "json_payloads_parsed": 0,
            "candidate_responses": 0,
            "array_shapes": [],
            "object_shapes": [],
        }

    def _write_status(self) -> None:
        status_path = self.output_dir / "capture-status.json"
        status_path.write_text(json.dumps(self.status, ensure_ascii=False, indent=2), encoding="utf-8")

    def _observe_shape(self, payload: Any) -> None:
        observations = self.status["array_shapes"]

        def walk(value: Any, path: str) -> None:
            if len(observations) >= 32:
                return
            if isinstance(value, dict):
                if len(self.status["object_shapes"]) < 32:
                    shape = {
                        "path": path,
                        "keys": sorted(map(str, value.keys()))[:24],
                        "types": {str(key): type(child).__name__ for key, child in value.items()},
                    }
                    if shape not in self.status["object_shapes"]:
                        self.status["object_shapes"].append(shape)
                for key, child in value.items():
                    walk(child, f"{path}.{key}")
            elif isinstance(value, list):
                records = [item for item in value if isinstance(item, dict)]
                if records:
                    keys = sorted({str(key) for record in records[:5] for key in record})[:24]
                    shape = {"path": path, "items": len(value), "record_keys": keys}
                    if shape not in observations:
                        observations.append(shape)
                for index, child in enumerate(value[:5]):
                    walk(child, f"{path}[{index}]")

        walk(payload, "$")

    def _write_seed_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return
        data = payload["data"]
        keys = ("map_seed", "map_seed_2", "map_md5", "need_wx_encrypt", "need_seed")
        if not any(key in data for key in ("map_seed", "map_seed_2")):
            return
        seed_only = {key: data[key] for key in keys if key in data}
        path = self.output_dir / "map-seed-payload.json"
        path.write_text(json.dumps(seed_only, ensure_ascii=False, indent=2), encoding="utf-8")

    def response(self, flow: Any) -> None:
        self.status["responses_seen"] += 1
        content_type = flow.response.headers.get("content-type", "").lower()
        if "json" not in content_type or len(flow.response.content) > 1_000_000:
            self._write_status()
            return
        self.status["json_responses_seen"] += 1
        try:
            payload = redact(json.loads(flow.response.get_text(strict=False)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_status()
            return
        self.status["json_payloads_parsed"] += 1
        self._observe_shape(payload)
        self._write_seed_payload(payload)
        candidates = extract_candidates(payload)
        if not candidates:
            self._write_status()
            return
        self.status["candidate_responses"] += 1
        record = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_url": strip_query(flow.request.pretty_url),
            "candidates": [candidate.to_dict() for candidate in candidates],
        }
        filename = self.output_dir / f"layout-candidate-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        filename.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_status()


def addon(output_dir: str) -> LayoutOnlyCapture:
    return LayoutOnlyCapture(output_dir)


class CaptureAddon:
    """mitmproxy lifecycle wrapper with a configurable local output directory."""

    def __init__(self) -> None:
        self.capture = LayoutOnlyCapture("captured-layouts")

    def load(self, loader: Any) -> None:
        loader.add_option(
            name="layout_output_dir",
            typespec=str,
            default="captured-layouts",
            help="Directory for redacted layout candidates only.",
        )

    def configure(self, updated: set[str]) -> None:
        if "layout_output_dir" in updated:
            from mitmproxy import ctx

            self.capture = LayoutOnlyCapture(ctx.options.layout_output_dir)

    def response(self, flow: Any) -> None:
        self.capture.response(flow)


addons = [CaptureAddon()]
