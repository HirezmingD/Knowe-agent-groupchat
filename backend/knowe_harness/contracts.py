from __future__ import annotations

"""Small deterministic helpers shared by the Harness boundaries.

The task model itself lives in :mod:`backend.runtime` as ``TaskEnvelope``.
This module intentionally contains no second task contract.
"""

import dataclasses
import hashlib
import json
import posixpath
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import unquote


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_identifier(prefix: str, value: Any, *, length: int = 24) -> str:
    return f"{prefix}_{content_sha256(value)[:length]}"


def normalize_relative_path(value: str) -> str:
    """Return one strict NFC/POSIX project-relative path."""

    raw = unicodedata.normalize("NFC", str(value or "").strip().strip("`\"'《》"))
    for _ in range(3):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    if not raw:
        raise ValueError("path must be non-empty")
    if "\x00" in raw or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ValueError("path contains a control character")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw):
        raise ValueError(f"URL is not a project-relative path: {value!r}")
    if raw.startswith(("//", "\\", "/", "~")):
        raise ValueError(f"absolute/UNC path is forbidden: {value!r}")
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        raise ValueError(f"drive-qualified path is forbidden: {value!r}")

    raw = raw.replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or raw.endswith("/"):
        raise ValueError(f"artifact path must identify a file: {value!r}")
    if "//" in raw:
        raise ValueError(f"repeated path separator is forbidden: {value!r}")
    if any(char in raw for char in "*?[]{}"):
        raise ValueError(f"glob syntax is forbidden in artifact paths: {value!r}")

    windows_reserved = {
        "con", "prn", "aux", "nul", "clock$",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    for part in raw.split("/"):
        if part in {"", ".", ".."}:
            raise ValueError(f"path traversal is forbidden: {value!r}")
        if len(part.encode("utf-8")) > 255:
            raise ValueError(f"path component is too long: {part[:40]!r}")
        if part.endswith((" ", ".")):
            raise ValueError(f"path component may not end in dot/space: {part!r}")
        if any(char in part for char in '<>:"|'):
            raise ValueError(f"path contains a platform-illegal character: {part!r}")
        if part.split(".", 1)[0].casefold() in windows_reserved:
            raise ValueError(f"reserved platform filename is forbidden: {part!r}")

    normalized = posixpath.normpath(raw)
    if normalized != raw or normalized in {"", "."}:
        raise ValueError(f"path is not in canonical project-relative form: {value!r}")
    if len(normalized.encode("utf-8")) > 4096:
        raise ValueError("path is too long")
    return normalized


__all__ = [
    "canonical_json",
    "content_sha256",
    "normalize_relative_path",
    "stable_identifier",
    "utc_now",
]
