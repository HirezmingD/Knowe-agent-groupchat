"""Task-local business-tool audit trail.

Phase F removes completion, evidence and progress authority from this module.  The
WorkerRuntime owns those decisions; this ledger only records what a legacy business
handler was asked to do and what it returned.  Coordinator tools may use the same
lightweight audit hook without acquiring Worker lifecycle semantics.
"""

from __future__ import annotations

import contextvars
from .i18n_backend import msg
import inspect
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterator, Mapping

ActivityEmitter = Callable[[str], Awaitable[None] | None]
Handler = Callable[..., Any]

TOOL_ACTIVITY_SEPARATOR = "\u241f"

_TOOL_STAGE: dict[str, str] = {
    # Worker fixed 19 tools.
    "safe_list_dir": "explore",
    "safe_search_files": "explore",
    "list_external_dir": "explore",
    "web_search": "explore",
    "browser_navigate": "explore",
    "safe_read_file": "integrate",
    "read_external_file": "integrate",
    "web_extract": "integrate",
    "browser_snapshot": "integrate",
    "browser_scroll": "integrate",
    "browser_back": "integrate",
    "safe_write_file": "implement",
    "safe_patch": "implement",
    "safe_delete_file": "implement",
    "copy_external_file": "implement",
    "browser_click": "implement",
    "browser_type": "implement",
    "safe_bash": "verify",
    "browser_screenshot": "verify",
    # Coordinator/business tools sharing the same visible event channel.
    "propose_agents": "plan",
    "propose_next": "plan",
    "propose_remove_agent": "plan",
    "read_report": "review",
    "submit_report": "deliver",
    "speak": "deliver",
}

_STAGE_DETAILS: dict[str, str] = {
    "explore": "stage.explore",
    "integrate": "stage.integrate",
    "plan": "stage.plan",
    "implement": "stage.implement",
    "verify": "stage.verify",
    "review": "stage.review",
    "deliver": "stage.deliver",
    "wait": "stage.wait",
}


def base_tool_name(value: str) -> str:
    """Return the observable tool name without its compact technical detail."""

    text = str(value or "").strip()
    if TOOL_ACTIVITY_SEPARATOR in text:
        return text.split(TOOL_ACTIVITY_SEPARATOR, 1)[0].strip()
    if "：" in text:
        candidate = text.split("：", 1)[0].strip()
        if candidate.replace("_", "").isalnum():
            return candidate
    return text


def stage_for_tool(value: str) -> str:
    """Map one real tool event to a public, live-only work stage."""

    name = base_tool_name(value)
    if name in _TOOL_STAGE:
        return _TOOL_STAGE[name]
    if name.startswith("browser_") or name.startswith("web_"):
        return "explore"
    if name.startswith("safe_"):
        return "implement"
    if name.startswith("propose_"):
        return "plan"
    return "plan"


def stage_payload(value: str, *, state: str = "active") -> dict[str, str]:
    """Optional existing-event fields for the public stage UI; no hidden reasoning."""

    stage = stage_for_tool(value)
    return {
        "stage": stage,
        "stage_detail": msg(_STAGE_DETAILS[stage]),
        "stage_state": state,
    }


@dataclass
class ToolAudit:
    actions: list[dict[str, Any]] = field(default_factory=list)
    activity_emitter: ActivityEmitter | None = None
    emitted_tokens: set[str] = field(default_factory=set)


# Compatibility type name for code that only inspects ``actions``.
TurnLedger = ToolAudit
_CURRENT: contextvars.ContextVar[ToolAudit | None] = contextvars.ContextVar(
    "knowe_tool_audit", default=None
)


def begin_turn() -> ToolAudit:
    """Compatibility helper for legacy callers.

    New execution boundaries should use :func:`actor_scope`, which restores the
    previous ContextVar value on exit.  ``begin_turn`` intentionally remains a
    one-way setter for old tests/callers that inspect the current audit directly.
    """

    audit = ToolAudit()
    _CURRENT.set(audit)
    return audit


@contextmanager
def actor_scope(
    *, activity_emitter: ActivityEmitter | None = None,
) -> Iterator[ToolAudit]:
    """Create one Actor-local audit context and restore the parent on exit.

    ``asyncio.create_task`` copies ContextVar *references*.  Reusing a mutable
    ``ToolAudit`` therefore lets a child Worker inherit the Coordinator's visible
    activity callback.  Replacing the object at every Actor boundary keeps the
    audit facts task-local while ensuring a Worker never emits through its parent's
    callback.  Worker Runtime activity remains owned by Runtime events; callers
    should leave ``activity_emitter`` unset for Worker attempts.
    """

    audit = ToolAudit(activity_emitter=activity_emitter)
    token = _CURRENT.set(audit)
    try:
        yield audit
    finally:
        _CURRENT.reset(token)


def current() -> ToolAudit | None:
    return _CURRENT.get()


def bind_activity(emitter: ActivityEmitter | None) -> None:
    audit = current()
    if audit is not None:
        audit.activity_emitter = emitter


def _one_line(value: Any, limit: int = 180) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def _ok(value: Any) -> tuple[bool, str]:
    parsed: Mapping[str, Any] | None = value if isinstance(value, Mapping) else None
    if parsed is None and isinstance(value, str):
        try:
            candidate = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            candidate = None
        if isinstance(candidate, Mapping):
            parsed = candidate
    if parsed is None:
        return True, ""
    status = str(parsed.get("status") or "").lower()
    if parsed.get("ok") is False or status in {"error", "failed", "failure"}:
        return False, str(parsed.get("message") or parsed.get("error") or "tool failed")
    code = parsed.get("exit_code")
    if isinstance(code, int) and code != 0:
        return False, str(parsed.get("message") or f"exit code {code}")
    return True, ""


async def _emit(audit: ToolAudit, text: str) -> None:
    emitter = audit.activity_emitter
    if emitter is None:
        return
    result = emitter(text)
    if inspect.isawaitable(result):
        await result


def activity_token(name: str, args: Mapping[str, Any] | None = None) -> str:
    detail = ""
    if args:
        for key in ("path", "command", "query", "url", "destination", "source"):
            if args.get(key) not in (None, ""):
                detail = f"{TOOL_ACTIVITY_SEPARATOR}{_one_line(args[key], 96)}"
                break
    return f"{name}{detail}"


def summarize_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit-only aggregate; it deliberately makes no completion judgement."""

    return {
        "calls": len(actions),
        "succeeded": sum(1 for item in actions if item.get("ok") is True),
        "failed": sum(1 for item in actions if item.get("ok") is False),
        "tools": [str(item.get("name") or "") for item in actions],
        "duration_ms": sum(int(item.get("duration_ms") or 0) for item in actions),
    }


def instrument(name: str, handler: Handler) -> Handler:
    """Wrap one handler with start/finish audit records and optional activity text."""

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        audit = current()
        mapping = args[0] if args and isinstance(args[0], Mapping) else kwargs
        token = activity_token(name, mapping if isinstance(mapping, Mapping) else None)
        started = time.monotonic()
        if audit is not None and token not in audit.emitted_tokens:
            audit.emitted_tokens.add(token)
            await _emit(audit, token)
        try:
            value = handler(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
        except BaseException as exc:
            if audit is not None:
                audit.actions.append(
                    {
                        "name": name,
                        "args": dict(mapping) if isinstance(mapping, Mapping) else {},
                        "ok": False,
                        "error": str(exc),
                        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    }
                )
            raise
        ok, error = _ok(value)
        if audit is not None:
            audit.actions.append(
                {
                    "name": name,
                    "args": dict(mapping) if isinstance(mapping, Mapping) else {},
                    "ok": ok,
                    "error": error,
                    "result": _one_line(value, 500),
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                }
            )
        return value

    wrapped.__name__ = getattr(handler, "__name__", f"audit_{name}")
    wrapped.__doc__ = getattr(handler, "__doc__", None)
    wrapped.__wrapped__ = handler
    return wrapped


__all__ = [
    "ToolAudit",
    "TOOL_ACTIVITY_SEPARATOR",
    "TurnLedger",
    "activity_token",
    "actor_scope",
    "begin_turn",
    "bind_activity",
    "current",
    "instrument",
    "base_tool_name",
    "stage_for_tool",
    "stage_payload",
    "summarize_actions",
]
