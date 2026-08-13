"""Central secret redaction for diagnostics that may cross trust boundaries."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_REDACTED = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)"
            r"(?:bearer\s+)?[^\s,;\"'}]+"
        ),
        rf"\1Bearer {_REDACTED}",
    ),
    (
        re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+\-/=]{6,}"),
        rf"\1{_REDACTED}",
    ),
    (
        re.compile(
            r"(?i)([\"']?(?:api[_-]?key|x-api-key|access[_-]?token|refresh[_-]?token|"
            r"auth[_-]?token|client[_-]?secret|password)[\"']?\s*[:=]\s*[\"']?)"
            r"[^\s,;\"'}&]+"
        ),
        rf"\1{_REDACTED}",
    ),
    (
        re.compile(
            r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret)=)"
            r"[^&#\s]+"
        ),
        rf"\1{_REDACTED}",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9._-]{8,}|github_pat_[A-Za-z0-9_]{12,}|"
            r"gh[pousr]_[A-Za-z0-9]{12,}|npm_[A-Za-z0-9]{12,}|"
            r"AIza[A-Za-z0-9_-]{16,})\b"
        ),
        _REDACTED,
    ),
)


def redact_sensitive_text(
    value: Any,
    *,
    secrets: Iterable[str] = (),
    limit: int | None = None,
) -> str:
    """Return a display-safe diagnostic without known or structurally likely secrets.

    Known values are replaced first so provider-specific key formats are covered even
    when they do not match a generic pattern.  Empty and very short strings are ignored
    to avoid erasing ordinary prose.
    """

    text = str(value or "")
    known = sorted(
        {str(secret) for secret in secrets if secret and len(str(secret)) >= 6},
        key=len,
        reverse=True,
    )
    for secret in known:
        text = text.replace(secret, _REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if limit is not None and len(text) > max(0, int(limit)):
        return text[: max(0, int(limit))].rstrip() + "…"
    return text


__all__ = ["redact_sensitive_text"]
