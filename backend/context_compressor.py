"""Single, non-destructive context projection for Coordinator provider calls.

Authoritative conversation history is never edited by this module.  When a provider
request needs a bounded projection, older messages are represented by stable source
references (or source ranges) and recent protocol-complete messages are carried verbatim.
The projection is explicitly labelled as non-authoritative and recoverable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Iterable

__all__ = [
    "CompressConfig",
    "DEFAULT_CONFIG",
    "SUMMARY_MARK",
    "classify_layer",
    "compress_messages",
    "is_scaffolding_user",
    "project_messages",
]

SUMMARY_MARK = "【上下文投影"
_SCAFFOLD_PREFIXES = (
    "⚠【系统内部指令",
    "⚠【执行提醒",
    "⚠【上一轮事故",
)


def is_scaffolding_user(content: Any) -> bool:
    """Recognize only explicit harness control-note prefixes."""

    return isinstance(content, str) and content.lstrip().startswith(_SCAFFOLD_PREFIXES)


def classify_layer(message: dict[str, Any]) -> str:
    """Return a diagnostic class without deleting or rewriting the message."""

    role = str(message.get("role") or "")
    if role == "user":
        return "L3_system" if is_scaffolding_user(message.get("content")) else "L1_user"
    if role == "assistant":
        calls = message.get("tool_calls") or []
        names = {
            str((call.get("function") or {}).get("name") or "")
            for call in calls
            if isinstance(call, dict)
        }
        if names & {"propose_next", "propose_agents", "propose_remove_agent"}:
            return "L1_decision"
        runtime = message.get("worker_runtime")
        runtime_status = (
            str(runtime.get("completion_status") or "").upper()
            if isinstance(runtime, dict)
            else ""
        )
        if (
            message.get("completion")
            or message.get("worker_completion")
            or runtime_status in {
                "SUCCEEDED", "PARTIAL", "WAITING", "BLOCKED", "FAILED",
                "CANCELLED", "SYSTEM_ERROR", "TIMED_OUT",
            }
        ):
            return "L1_deliverable"
    return "L2_exec"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class CompressConfig:
    """Provider projection policy.

    ``projection_chars`` is a request projection budget, not an authority-store limit.
    Legacy item-cap arguments are accepted for source compatibility and intentionally
    ignored: no category or per-item fact cap is applied.
    """

    def __init__(
        self,
        enabled: bool | None = None,
        trigger: int | None = None,
        keep_recent: int | None = None,
        projection_chars: int | None = None,
        max_user_reqs: int | None = None,
        max_decisions: int | None = None,
        max_deliverables: int | None = None,
        per_item_chars: int | None = None,
    ) -> None:
        del max_user_reqs, max_decisions, max_deliverables, per_item_chars
        raw = os.environ.get("KNOWE_CTX_COMPRESS", "1").strip().lower()
        self.enabled = raw not in {"0", "false", "off", "no"} if enabled is None else bool(enabled)
        self.trigger = max(1, _env_int("KNOWE_CTX_TRIGGER", 60) if trigger is None else int(trigger))
        self.keep_recent = max(
            1,
            _env_int("KNOWE_CTX_KEEP_RECENT", 30)
            if keep_recent is None
            else int(keep_recent),
        )
        self.projection_chars = max(
            4_096,
            _env_int("KNOWE_CTX_PROJECTION_CHARS", 180_000)
            if projection_chars is None
            else int(projection_chars),
        )


DEFAULT_CONFIG = CompressConfig()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _message_chars(message: dict[str, Any]) -> int:
    return len(_canonical(message))


def _message_ref(index: int) -> str:
    return f"conversation://message/{index + 1}"


def _range_ref(start: int, end: int) -> str:
    return f"conversation://messages/{start + 1}-{end + 1}"


def _clean_protocol_boundary(messages: list[dict[str, Any]], proposed: int) -> int:
    """Move a tail cut to a user boundary so tool_call/result pairs stay intact."""

    if proposed <= 0:
        return 0
    for index in range(proposed, len(messages)):
        if messages[index].get("role") == "user":
            return index
    for index in range(proposed - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return len(messages)


def _tail_start(messages: list[dict[str, Any]], cfg: CompressConfig) -> int:
    proposed = max(0, len(messages) - cfg.keep_recent)
    start = _clean_protocol_boundary(messages, proposed)

    # The recent verbatim tail is itself a projection.  If it exceeds its share, move the
    # cut forward by whole user turns; the omitted turn remains represented by refs.
    tail_budget = int(cfg.projection_chars * 0.68)
    while start < len(messages) and sum(_message_chars(m) for m in messages[start:]) > tail_budget:
        next_user = next(
            (i for i in range(start + 1, len(messages)) if messages[i].get("role") == "user"),
            None,
        )
        if next_user is None:
            break
        start = next_user
    return start


def _entry_text(message: dict[str, Any], index: int, *, include_payload: bool) -> str:
    role = str(message.get("role") or "unknown")
    ref = _message_ref(index)
    digest = _sha(message)
    chars = _message_chars(message)
    header = f"- source_ref={ref}; role={role}; chars={chars}; sha256={digest}"
    if not include_payload:
        return header + "; payload=authoritative-history"
    return header + "\n  payload=" + _canonical(message)


def _projection_index(
    messages: list[dict[str, Any]],
    end: int,
    budget: int,
) -> str:
    """Render recoverable refs for every older message, coalescing only as ranges."""

    if end <= 0:
        return ""
    lines: list[str] = []
    used = 0
    omitted_range_start: int | None = None

    for index, message in enumerate(messages[:end]):
        # Carry full payload while budget permits; otherwise retain a stable descriptor.
        full = _entry_text(message, index, include_payload=True)
        descriptor = _entry_text(message, index, include_payload=False)
        candidate = full if used + len(full) + 1 <= budget else descriptor
        if used + len(candidate) + 1 <= budget:
            if omitted_range_start is not None:
                range_end = index - 1
                range_line = (
                    f"- source_range={_range_ref(omitted_range_start, range_end)}; "
                    f"count={range_end - omitted_range_start + 1}; "
                    f"sha256={_sha(messages[omitted_range_start:index])}; "
                    "payload=authoritative-history"
                )
                lines.append(range_line)
                used += len(range_line) + 1
                omitted_range_start = None
            lines.append(candidate)
            used += len(candidate) + 1
        elif omitted_range_start is None:
            omitted_range_start = index

    if omitted_range_start is not None:
        range_end = end - 1
        lines.append(
            f"- source_range={_range_ref(omitted_range_start, range_end)}; "
            f"count={range_end - omitted_range_start + 1}; "
            f"sha256={_sha(messages[omitted_range_start:end])}; "
            "payload=authoritative-history"
        )
    return "\n".join(lines)


def _summary_message(
    messages: list[dict[str, Any]],
    head_end: int,
    cfg: CompressConfig,
) -> dict[str, Any]:
    from .i18n_backend import msg  # 局部导入：模块级 msg() 会固化语言
    header = (
        f"{msg('ctx.001')}{msg('ctx.002')}\n"
        f"projected_source_range={_range_ref(0, head_end - 1)}\n"
        f"projected_messages={head_end}; authoritative_messages={len(messages)}\n"
        + msg("ctx.003")
        + "\n"
        + msg("ctx.004")
        + "\n"
    )
    index_budget = max(512, int(cfg.projection_chars * 0.30) - len(header))
    content = header + _projection_index(messages, head_end, index_budget)
    return {
        "role": "user",
        "content": content,
        "metadata": {
            "projection": True,
            "authoritative": False,
            "source_ref": _range_ref(0, head_end - 1),
            "source_start": 1,
            "source_end": head_end,
            "source_count": head_end,
            "authoritative_count": len(messages),
        },
    }


def project_messages(
    messages: list[dict[str, Any]],
    cfg: CompressConfig | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return a provider projection and count of messages represented by refs.

    The input list and every contained message remain untouched.  A return count of zero
    means the full sequence is already within the configured projection conditions.
    """

    cfg = cfg or DEFAULT_CONFIG
    source = [copy.deepcopy(m) for m in messages if isinstance(m, dict)]
    if not cfg.enabled:
        return source, 0
    total_chars = sum(_message_chars(message) for message in source)
    if len(source) <= cfg.trigger and total_chars <= cfg.projection_chars:
        return source, 0

    start = _tail_start(source, cfg)
    if start <= 0:
        # One exceptionally large turn has no safe protocol boundary.  Represent the full
        # range by refs rather than slicing ordinary language inside that turn.
        start = len(source)
    summary = _summary_message(source, start, cfg)
    tail = source[start:]
    return [summary, *tail], start


def compress_messages(
    messages: list[dict[str, Any]],
    cfg: CompressConfig | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Backward-compatible alias for the single projection entry point."""

    return project_messages(messages, cfg)
