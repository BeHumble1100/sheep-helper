"""Remove credentials before a capture payload leaves process memory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "ticket",
    "openid",
    "unionid",
    "session",
    "signature",
    "sign",
    "phone",
    "mobile",
    "email",
    "password",
    "secret",
)


def is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("_", "").replace("-", "")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact(value: Any) -> Any:
    """Return a deep copy without sensitive map entries.

    Lists and scalar values are retained so that public board geometry remains
    usable. Query strings are never retained because they often contain tokens.
    """
    if isinstance(value, Mapping):
        return {str(key): redact(item) for key, item in value.items() if not is_sensitive_key(key)}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
