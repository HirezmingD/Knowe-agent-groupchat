# knowe v0.6 — Harness 核心引擎
"""
Stream assembler — accumulates provider deltas into one typed assistant turn.

The important boundary in this module is not JSON concatenation; it is the boundary
between the provider's *provisional* stream and Knowe's *public* chat channel.

A provider may put a tool control frame in ``delta.content`` instead of the native
``delta.tool_calls`` field.  Therefore content from a tool-capable turn is not public
text merely because it arrived through a field named ``content``.  In guarded mode we
buffer the complete turn, normalize its protocol, and only then commit one of these
mutually exclusive outcomes:

* public text;
* canonical tool calls;
* a quarantined protocol/stream error.

No byte from a guarded turn reaches ``stream_delta_callback`` before that decision.
This is the structural safety property that a streaming regex filter cannot provide.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from knowe_core.errors import StreamError, StreamParseError  # compatibility exports
from knowe_core.tool_protocol import (
    decode_json_object_arguments,
    decode_text_tool_protocol,
)

logger = logging.getLogger(__name__)

# Callback signatures (same as AgentPort):
#   stream_delta_callback(text: str) -> None
#   tool_gen_callback(tool_name: str) -> None
#   tool_start_callback() -> None
#   tool_complete_callback() -> None
StreamDeltaCB = Callable[[str], None]
ToolGenCB = Callable[[str], None]
VoidCB = Callable[[], None]

ProtocolMode = Literal["off", "normalize", "reject"]
TurnKind = Literal["text", "tool_calls", "empty", "protocol_error", "stream_error"]


@dataclass(frozen=True)
class AssembledTurn:
    """Typed result of one complete provider turn.

    ``message`` is kept for compatibility with the OpenAI conversation shape.  For a
    control turn its content is always ``None``: provider-side preambles and serialized
    protocols are private and never enter model history as public assistant prose.
    """

    kind: TurnKind
    message: dict[str, Any]
    content: str = ""
    reasoning: str = ""  # [v1.0.23.3] 完整推理文本（reasoning_content 累积）
    tool_calls: tuple[dict[str, Any], ...] = ()
    finish_reason: str | None = None
    error: str | None = None
    protocol_encoding: str = ""


class StreamAssembler:
    """Accumulate SSE events and finalize them through a typed protocol gate.

    ``tool_protocol_mode`` controls the content/tool boundary:

    ``off``
        Legacy behavior. Content callbacks fire immediately. Use only for turns that
        have no active tools and whose provider output is already an ordinary speech
        channel.
    ``normalize``
        Buffer the full turn. Native calls and supported text-encoded calls become
        canonical ``tool_calls``; only a proven plain-text turn is published.
    ``reject``
        Buffer the full turn and reject any tool call. This is useful for a turn whose
        request intentionally exposes no action tools.

    ``require_typed_output`` is a stricter policy for agents such as Zinnia: even plain
    provider content is untrusted. The agent must use an explicit public-reply function,
    so arbitrary ``delta.content`` can never become user-visible by accident.
    """

    def __init__(
        self,
        stream_delta_callback: StreamDeltaCB | None = None,
        reasoning_delta_callback: StreamDeltaCB | None = None,  # [v1.0.23.3]
        tool_gen_callback: ToolGenCB | None = None,
        tool_start_callback: VoidCB | None = None,
        tool_complete_callback: VoidCB | None = None,
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
        tool_protocol_mode: ProtocolMode = "off",
        require_typed_output: bool = False,
        max_content_chars: int = 1_000_000,
    ):
        if tool_protocol_mode not in {"off", "normalize", "reject"}:
            raise ValueError(f"unknown tool_protocol_mode: {tool_protocol_mode!r}")
        if require_typed_output and tool_protocol_mode == "off":
            raise ValueError("require_typed_output needs a guarded protocol mode")
        if int(max_content_chars) <= 0:
            raise ValueError("max_content_chars must be positive")

        self._stream_delta_cb = stream_delta_callback
        self._reasoning_delta_cb = reasoning_delta_callback  # [v1.0.23.3]
        self._tool_gen_cb = tool_gen_callback
        self._tool_start_cb = tool_start_callback
        self._tool_complete_cb = tool_complete_callback
        self._tool_schemas = list(tool_schemas or [])
        self._tool_protocol_mode = tool_protocol_mode
        self._require_typed_output = require_typed_output
        self._active_tool_names = self._schema_names(self._tool_schemas)
        self._max_content_chars = int(max_content_chars)
        self._content_chars = 0
        self._content_overflow = False

        # Provisional content. In guarded mode this is deliberately not public yet.
        self.content_parts: list[str] = []

        # [v1.0.23.3] 推理累积：不经过 guarded 闸门（推理发生在工具调用前，天然安全），实时回调
        self.reasoning_parts: list[str] = []

        # Accumulated native tool calls: {index: {id, type, function.{name,args}}}
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._announced_tool_names: set[str] = set()
        self._tool_phase_started = False
        self._tool_phase_completed = False

        self.finish_reason: str | None = None
        self.error: str | None = None
        self._finalized: AssembledTurn | None = None

    # ── Feed events ──

    def feed(self, event: dict[str, Any]) -> None:
        """Process one normalized event from ``ProviderClient.chat_stream()``."""
        if self._finalized is not None:
            raise RuntimeError("cannot feed a finalized StreamAssembler")

        etype = event.get("type", "")
        if etype == "delta":
            self._handle_delta(event.get("content", ""))
        elif etype == "reasoning_delta":  # [v1.0.23.3]
            self._handle_reasoning_delta(event.get("content", ""))
        elif etype == "tool_call":
            self._handle_tool_call(event.get("tool_call", {}))
        elif etype == "finish":
            reason = event.get("reason")
            if isinstance(reason, str) and reason:
                self.finish_reason = reason
        elif etype == "error":
            self.error = str(event.get("message") or "unknown stream error")
            logger.error("StreamAssembler: %s", self.error)

    # ── Finalize ──

    def finalize_turn(self) -> AssembledTurn:
        """Classify and freeze the complete provider turn.

        This method is idempotent. In guarded mode it is the *only* place allowed to
        commit text to ``stream_delta_callback``.
        """
        if self._finalized is not None:
            return self._finalized

        content = "".join(self.content_parts)
        native_calls = self._finalize_native_tool_calls()

        if self.error:
            self._close_tool_phase()
            return self._remember(self._error_turn("stream_error", self.error))

        if self._content_overflow:
            self._close_tool_phase()
            return self._remember(self._error_turn(
                "protocol_error",
                f"stream content exceeded {self._max_content_chars} characters",
            ))

        if native_calls:
            if self._tool_protocol_mode == "reject":
                self._close_tool_phase()
                return self._remember(self._error_turn(
                    "protocol_error",
                    "provider returned a tool call in a turn where tool calls are forbidden",
                ))
            validation_error = self._validate_tool_calls(native_calls)
            if validation_error:
                self._close_tool_phase()
                return self._remember(self._error_turn("protocol_error", validation_error))

            self._announce_calls(native_calls)
            self._close_tool_phase()
            if self._tool_protocol_mode == "off":
                # Backward-compatible default: legacy callers may intentionally keep a
                # textual preamble beside native calls, and those deltas were already
                # emitted during feed(). Guarded callers never take this branch.
                return self._remember(self._tool_turn(
                    native_calls, encoding="native", public_content=content,
                ))

            # In guarded mode a native tool-call turn is control-plane data. Any
            # accompanying provider preamble stays private.
            return self._remember(self._tool_turn(native_calls, encoding="native"))

        if self._tool_protocol_mode != "off":
            decoded = decode_text_tool_protocol(
                content,
                self._tool_schemas,
                finish_reason=self.finish_reason,
            )
            if decoded.kind == "invalid":
                self._close_tool_phase()
                return self._remember(self._error_turn(
                    "protocol_error",
                    decoded.reason or "provider returned an invalid tool control frame",
                    encoding=decoded.encoding,
                ))
            if decoded.kind == "tool_calls":
                calls = list(decoded.tool_calls)
                if self._tool_protocol_mode == "reject":
                    self._close_tool_phase()
                    return self._remember(self._error_turn(
                        "protocol_error",
                        "provider serialized a tool call in a turn where tool calls are forbidden",
                        encoding=decoded.encoding,
                    ))
                validation_error = self._validate_tool_calls(calls)
                if validation_error:
                    self._close_tool_phase()
                    return self._remember(self._error_turn(
                        "protocol_error", validation_error, encoding=decoded.encoding,
                    ))
                self._announce_calls(calls)
                self._close_tool_phase()
                return self._remember(self._tool_turn(calls, encoding=decoded.encoding))

            if self._require_typed_output and content.strip():
                return self._remember(self._error_turn(
                    "protocol_error",
                    "untyped assistant content is not publishable in this turn",
                ))

            self._commit_public_text(content)
            return self._remember(self._text_turn(content))

        # Legacy/plain turn: deltas were already committed during feed().
        return self._remember(self._text_turn(content))

    def finalize(self) -> dict[str, Any]:
        """Backward-compatible message-only wrapper around :meth:`finalize_turn`."""
        return self.finalize_turn().message

    # ── Internal handlers ──

    def _handle_delta(self, text: Any) -> None:
        if not isinstance(text, str) or not text:
            return
        remaining = self._max_content_chars - self._content_chars
        accepted = text[:max(0, remaining)]
        if accepted:
            self.content_parts.append(accepted)
            self._content_chars += len(accepted)
            if self._tool_protocol_mode == "off":
                self._safe_callback(self._stream_delta_cb, accepted, label="stream_delta")
        if len(text) > len(accepted):
            self._content_overflow = True

    def _handle_reasoning_delta(self, text: Any) -> None:  # [v1.0.23.3]
        """推理增量：直接累积 + 实时回调（推理先于正文/工具，不受 guarded 闸限制）。"""
        if not isinstance(text, str) or not text:
            return
        self.reasoning_parts.append(text)
        self._safe_callback(self._reasoning_delta_cb, text, label="reasoning_delta")

    def _handle_tool_call(self, tc_delta: dict[str, Any]) -> None:
        if not isinstance(tc_delta, dict):
            self.error = "tool_call delta is not an object"
            return
        idx = tc_delta.get("index", 0)
        if not isinstance(idx, int) or idx < 0:
            idx = 0
        tc_id = tc_delta.get("id")

        if idx not in self._tool_calls:
            self._tool_calls[idx] = {
                "id": tc_id if isinstance(tc_id, str) else None,
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
        tc = self._tool_calls[idx]

        if isinstance(tc_id, str) and tc_id:
            tc["id"] = tc_id

        func = tc_delta.get("function")
        if not isinstance(func, dict):
            func = {}
        name = func.get("name")
        if isinstance(name, str) and name:
            # Compatible gateways use both incremental ("web_" + "search") and
            # cumulative ("web_" then "web_search") name fragments. Normalize both,
            # but do not announce a provisional name before the complete turn exists.
            current = str(tc["function"].get("name") or "")
            tc["function"]["name"] = self._merge_stream_fragment(current, name)
            if self._tool_protocol_mode == "off":
                self._announce_tool(str(tc["function"].get("name") or name))

        args_fragment = func.get("arguments", "")
        if args_fragment is not None:
            if not isinstance(args_fragment, str):
                try:
                    args_fragment = json.dumps(args_fragment, ensure_ascii=False)
                except (TypeError, ValueError):
                    args_fragment = str(args_fragment)
            current_args = str(tc["function"].get("arguments") or "")
            tc["function"]["arguments"] = self._merge_stream_fragment(
                current_args, args_fragment,
            )


    @staticmethod
    def _merge_stream_fragment(current: str, fragment: str) -> str:
        """Merge incremental *or cumulative* provider fragments without duplication."""
        if not fragment:
            return current
        if not current or fragment.startswith(current):
            return fragment
        # Do not infer a suffix/prefix overlap for ordinary incremental chunks.  JSON
        # strings legitimately split as ``"hel"`` + ``"lo"``; overlap-elision would
        # silently corrupt that into ``"helo"``.  Exact cumulative and duplicate
        # fragments are handled above, all other fragments are appended verbatim.
        return current + fragment

    # ── Turn constructors ──

    def _text_turn(self, content: str) -> AssembledTurn:
        kind: TurnKind = "text" if content else "empty"
        return AssembledTurn(
            kind=kind,
            message={"role": "assistant", "content": content or None},
            content=content,
            reasoning=self._reasoning_text(),  # [v1.0.23.3]
            finish_reason=self.finish_reason,
        )

    def _tool_turn(
        self,
        calls: list[dict[str, Any]],
        *,
        encoding: str,
        public_content: str = "",
    ) -> AssembledTurn:
        frozen_calls = tuple(calls)
        return AssembledTurn(
            kind="tool_calls",
            message={
                "role": "assistant",
                "content": public_content or None,
                "tool_calls": list(frozen_calls),
            },
            content=public_content,
            reasoning=self._reasoning_text(),  # [v1.0.23.3]
            tool_calls=frozen_calls,
            finish_reason=self.finish_reason,
            protocol_encoding=encoding,
        )

    def _error_turn(
        self,
        kind: Literal["protocol_error", "stream_error"],
        reason: str,
        *,
        encoding: str = "",
    ) -> AssembledTurn:
        return AssembledTurn(
            kind=kind,
            message={"role": "assistant", "content": None},
            reasoning=self._reasoning_text(),  # [v1.0.23.3] 错误回合也保留已收到的推理
            finish_reason=self.finish_reason,
            error=reason,
            protocol_encoding=encoding,
        )

    def _reasoning_text(self) -> str:  # [v1.0.23.3]
        return "".join(self.reasoning_parts)

    def _remember(self, turn: AssembledTurn) -> AssembledTurn:
        self._finalized = turn
        return turn

    # ── Tool-call normalization and validation ──

    def _finalize_native_tool_calls(self) -> list[dict[str, Any]]:
        if not self._tool_calls:
            return []
        result: list[dict[str, Any]] = []
        for idx in sorted(self._tool_calls):
            tc = self._tool_calls[idx]
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            args_raw = (fn or {}).get("arguments", "")
            if not isinstance(args_raw, str):
                args_raw = json.dumps(args_raw, ensure_ascii=False)
            args_raw = args_raw or "{}"
            parsed, _repaired = decode_json_object_arguments(args_raw)
            if parsed is None:
                normalized_args = args_raw
            else:
                normalized_args = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            result.append({
                "id": str(tc.get("id") or f"call_{idx}"),
                "type": "function",
                "function": {"name": name, "arguments": normalized_args},
            })
        return result

    def _validate_tool_calls(self, calls: list[dict[str, Any]]) -> str | None:
        if not calls:
            return "tool-call turn contains no calls"
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                return f"tool call {index} is not an object"
            fn = call.get("function")
            if not isinstance(fn, dict):
                return f"tool call {index} has no function object"
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                return f"tool call {index} has no function name"
            if self._active_tool_names and name not in self._active_tool_names:
                return f"tool call references an unavailable function: {name}"
            raw = fn.get("arguments", "{}")
            if isinstance(raw, dict):
                parsed = raw
                fn["arguments"] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(raw, str):
                parsed, repaired = decode_json_object_arguments(raw or "{}")
                if parsed is None:
                    return f"tool call {name} has malformed JSON arguments"
                if repaired:
                    fn["arguments"] = json.dumps(
                        parsed,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
            else:
                return f"tool call {name} arguments are not an object"
            if not isinstance(parsed, dict):
                return f"tool call {name} arguments must decode to an object"
        return None

    @staticmethod
    def _schema_names(schemas: list[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for item in schemas:
            if not isinstance(item, dict):
                continue
            fn = item.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
                if isinstance(name, str) and name:
                    names.add(name)
        return names

    # ── Callback lifecycle ──

    def _commit_public_text(self, content: str) -> None:
        if content:
            self._safe_callback(self._stream_delta_cb, content, label="stream_delta")

    def _announce_calls(self, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            fn = call.get("function") if isinstance(call, dict) else None
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and name:
                self._announce_tool(name)

    def _announce_tool(self, name: str) -> None:
        if name in self._announced_tool_names:
            return
        self._announced_tool_names.add(name)
        if not self._tool_phase_started:
            self._tool_phase_started = True
            self._safe_callback(self._tool_start_cb, label="tool_start")
        self._safe_callback(self._tool_gen_cb, name, label="tool_gen")

    def _close_tool_phase(self) -> None:
        if self._tool_phase_started and not self._tool_phase_completed:
            self._tool_phase_completed = True
            self._safe_callback(self._tool_complete_cb, label="tool_complete")

    @staticmethod
    def _safe_callback(callback: Callable[..., Any] | None, *args: Any, label: str) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            logger.exception("%s_callback failed", label)

    def reset(self) -> None:
        """Reset all accumulated state for a new turn."""
        self.content_parts.clear()
        self.reasoning_parts.clear()  # [v1.0.23.3]
        self._tool_calls.clear()
        self._announced_tool_names.clear()
        self._tool_phase_started = False
        self._tool_phase_completed = False
        self._content_chars = 0
        self._content_overflow = False
        self.finish_reason = None
        self.error = None
        self._finalized = None


__all__ = ["AssembledTurn", "StreamAssembler"]
