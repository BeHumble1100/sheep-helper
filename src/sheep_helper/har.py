"""HAR import that exposes only redacted decoded JSON payloads."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterator

from .redaction import redact, strip_query


def iter_har_payloads(path: Path) -> Iterator[tuple[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("log", {}).get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("HAR log.entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        response = entry.get("response", {})
        content = response.get("content", {}) if isinstance(response, dict) else {}
        text = content.get("text") if isinstance(content, dict) else None
        if not isinstance(text, str):
            continue
        if content.get("encoding") == "base64":
            try:
                text = base64.b64decode(text, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        request = entry.get("request", {})
        url = request.get("url", "") if isinstance(request, dict) else ""
        yield strip_query(str(url)), redact(payload)
