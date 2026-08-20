from __future__ import annotations

"""Provider boundary for the minimal Worker Runtime.

One call produces one :class:`StepOutcome`.  Only provider-native ``tool_calls`` (or
legacy provider ``function_call`` control fields) become executable calls.  Text content
is never parsed as XML, Markdown, JSON, or function syntax.
"""

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from backend.runtime import (
    ModelUsage,
    OutcomeKind,
    RuntimeContext,
    StepOutcome,
    ToolCall,
)
from knowe_core.provider_client import normalize_usage_buckets


TextCallback = Callable[[str], Awaitable[None] | None]


class ProviderProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderAdapterConfig:
    stream: bool = True
    # v1.0.19.5: 模型预设参数一律不传（与全局删参决策一致）——提供商默认什么就用什么。
    #   Kimi 强制 temperature=1，任何非 1 预设都会 400。
    temperature: float | None = None
    max_tokens: int | None = None
    extra_body: dict[str, Any] | None = None


class ProviderModelAdapter:
    """Adapt an OpenAI-compatible provider client without owning an agent loop."""

    def __init__(
        self,
        client: Any,
        *,
        config: ProviderAdapterConfig | None = None,
        on_text: TextCallback | None = None,
    ) -> None:
        if not (hasattr(client, "chat") or hasattr(client, "chat_stream")):
            raise TypeError("provider client must expose chat and/or chat_stream")
        self.client = client
        self.config = config or ProviderAdapterConfig()
        self.on_text = on_text
        self.calls = 0

    @classmethod
    def from_legacy(cls, source: Any, **kwargs: Any) -> "ProviderModelAdapter":
        """Extract a provider client from the existing Agent object, never its loop."""

        if hasattr(source, "chat") or hasattr(source, "chat_stream"):
            return cls(source, **kwargs)
        client = getattr(source, "_client", None)
        if client is not None and (hasattr(client, "chat") or hasattr(client, "chat_stream")):
            config = kwargs.pop("config", None)
            if config is None:
                provider_cfg = getattr(source, "_provider_cfg", None)
                config = ProviderAdapterConfig(
                    stream=True,
                    temperature=getattr(provider_cfg, "temperature", None),
                    max_tokens=None,
                )
            return cls(client, config=config, **kwargs)

        base_url = str(getattr(source, "base_url", "") or "")
        if base_url:
            from knowe_core.provider_client import ProviderClient

            # 从 source 携带 transport（ProviderConfig 或 source 本身上）：legacy 重建
            # 分支也必须保持协议一致，否则 anthropic 主模型会退化成 openai 传输而打错端点。
            provider_cfg = getattr(source, "_provider_cfg", None)
            cfg_transport = (
                getattr(provider_cfg, "transport", "") if provider_cfg is not None else ""
            )
            src_transport = getattr(source, "transport", "") or ""
            transport = (cfg_transport or src_transport) or "openai_chat"
            return cls(
                ProviderClient(
                    base_url=base_url,
                    api_key=str(getattr(source, "api_key", "") or ""),
                    model=str(getattr(source, "model", "") or ""),
                    client_factory=getattr(source, "_client_factory", None),
                    transport=transport,
                ),
                **kwargs,
            )
        raise TypeError("could not extract a provider client from legacy source")

    async def step(
        self,
        context: RuntimeContext,
        reasoning_delta_callback: TextCallback | None = None,  # [v1.0.23.3] worker 推理透传
        **_ignored: Any,
    ) -> StepOutcome:
        self.calls += 1
        messages = [dict(item) for item in context.messages]
        tools = [dict(item) for item in context.tool_schemas]
        if self.config.stream and hasattr(self.client, "chat_stream"):
            outcome = await self._stream_step(
                messages, tools, reasoning_delta_callback=reasoning_delta_callback
            )
        else:
            if not hasattr(self.client, "chat"):
                raise TypeError("provider client has no non-stream chat method")
            response = await self.client.chat(
                messages,
                tools=tools,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_body=dict(self.config.extra_body or {}),
            )
            outcome = self.normalize_response(response)
        if outcome.kind is OutcomeKind.FINAL and outcome.assistant_text and self.on_text:
            value = self.on_text(outcome.assistant_text)
            if inspect.isawaitable(value):
                await value
        return outcome

    async def _stream_step(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        reasoning_delta_callback: TextCallback | None = None,  # [v1.0.23.3]
    ) -> StepOutcome:
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        usage: ModelUsage | None = None
        async for event in self.client.chat_stream(
            messages,
            tools=tools,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            extra_body=dict(self.config.extra_body or {}),
        ):
            if not isinstance(event, Mapping):
                raise ProviderProtocolError("provider stream event is not an object")
            event_type = str(event.get("type") or "")
            if event_type == "delta":
                text = event.get("content")
                if isinstance(text, str):
                    content.append(text)
            elif event_type == "reasoning_delta":
                # [v1.0.23.3] worker 推理透传：provider 的 reasoning_content 增量
                #   实时回调（此前被静默忽略，worker 气泡只有三点动画）。
                text = event.get("content")
                if isinstance(text, str) and text:
                    reasoning.append(text)
                    if reasoning_delta_callback is not None:
                        value = reasoning_delta_callback(text)
                        if inspect.isawaitable(value):
                            await value
            elif event_type == "tool_call":
                self._merge_tool_delta(calls, event.get("tool_call"))
            elif event_type == "finish":
                finish_reason = str(event.get("reason") or "")
            elif event_type == "usage":
                # [M1 采集点 B] Streaming gateways emit usage in a terminal frame.
                # A stream may carry several partial fragments, so merge by summation
                # rather than last-wins.
                raw_usage = event.get("usage")
                buckets = normalize_usage_buckets(raw_usage)
                if buckets is not None:
                    fragment = ModelUsage(
                        input_tokens=buckets["cache_hit_input"] + buckets["cache_miss_input"],
                        output_tokens=buckets["output"],
                        cache_read_tokens=buckets["cache_hit_input"],
                        raw=dict(raw_usage) if isinstance(raw_usage, Mapping) else {},
                    )
                    usage = (
                        fragment
                        if usage is None
                        else ModelUsage(
                            input_tokens=usage.input_tokens + fragment.input_tokens,
                            output_tokens=usage.output_tokens + fragment.output_tokens,
                            reasoning_tokens=usage.reasoning_tokens + fragment.reasoning_tokens,
                            cache_read_tokens=usage.cache_read_tokens + fragment.cache_read_tokens,
                            cache_write_tokens=usage.cache_write_tokens + fragment.cache_write_tokens,
                            cost_usd=usage.cost_usd + fragment.cost_usd,
                            raw=usage.raw,
                        )
                    )
            elif event_type == "error":
                raise ProviderProtocolError(str(event.get("message") or "provider stream error"))
        native_calls = self._finalize_stream_calls(calls)
        text = "".join(content)
        reasoning_text = "".join(reasoning)
        # [v1.0.23.3] 推理文本随 outcome 带出（metadata，供落定链路后续消费）
        reasoning_meta = {"reasoning": reasoning_text} if reasoning_text else {}
        if native_calls:
            return StepOutcome(
                OutcomeKind.ACTIONS,
                assistant_text=text,
                tool_calls=native_calls,
                usage=usage or ModelUsage(),
                metadata={
                    "finish_reason": finish_reason,
                    "provider_protocol": "native",
                    **reasoning_meta,
                },
            )
        return StepOutcome.final(
            text,
            usage=usage or ModelUsage(),
            finish_reason=finish_reason,
            **reasoning_meta,
        )

    @staticmethod
    def _merge_tool_delta(target: dict[int, dict[str, Any]], raw: Any) -> None:
        if not isinstance(raw, Mapping):
            raise ProviderProtocolError("provider tool-call delta is not an object")
        index = raw.get("index", 0)
        if not isinstance(index, int) or index < 0:
            index = 0
        row = target.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if isinstance(raw.get("id"), str) and raw.get("id"):
            row["id"] = str(raw["id"])
        function = raw.get("function")
        if not isinstance(function, Mapping):
            function = raw
        name = function.get("name")
        arguments = function.get("arguments")
        if isinstance(name, str):
            row["function"]["name"] += name
        if isinstance(arguments, str):
            row["function"]["arguments"] += arguments
        elif isinstance(arguments, Mapping):
            row["function"]["arguments"] = json.dumps(dict(arguments), ensure_ascii=False)

    @staticmethod
    def _finalize_stream_calls(rows: Mapping[int, Mapping[str, Any]]) -> tuple[ToolCall, ...]:
        output: list[ToolCall] = []
        for index in sorted(rows):
            try:
                output.append(ToolCall.from_provider(rows[index], index=index))
            except (TypeError, ValueError) as exc:
                raise ProviderProtocolError(str(exc)) from exc
        return tuple(output)

    @classmethod
    def normalize_response(cls, response: Any) -> StepOutcome:
        if isinstance(response, StepOutcome):
            return response
        mapping = cls._as_mapping(response)
        explicit_kind = str(mapping.get("kind") or "").strip().upper()
        if explicit_kind in {item.value for item in OutcomeKind}:
            return cls._explicit_outcome(mapping, explicit_kind)

        message = cls._message_mapping(mapping)
        text = cls._content_text(message.get("content"))
        calls = cls._native_tool_calls(message)
        usage = ModelUsage.from_mapping(cls._as_mapping(mapping.get("usage")))
        finish_reason = cls._finish_reason(mapping)
        if calls:
            return StepOutcome(
                OutcomeKind.ACTIONS,
                assistant_text=text,
                tool_calls=calls,
                usage=usage,
                metadata={"finish_reason": finish_reason, "provider_protocol": "native"},
            )
        return StepOutcome.final(text, usage=usage, finish_reason=finish_reason)

    @classmethod
    def _explicit_outcome(cls, mapping: Mapping[str, Any], kind: str) -> StepOutcome:
        usage = ModelUsage.from_mapping(cls._as_mapping(mapping.get("usage")))
        if kind == OutcomeKind.ACTIONS.value:
            raw_calls = mapping.get("tool_calls") or ()
            calls = tuple(
                item if isinstance(item, ToolCall) else ToolCall.from_provider(cls._as_mapping(item), index)
                for index, item in enumerate(raw_calls if isinstance(raw_calls, Sequence) else ())
            )
            return StepOutcome(
                OutcomeKind.ACTIONS,
                assistant_text=str(mapping.get("assistant_text") or mapping.get("content") or ""),
                tool_calls=calls,
                metadata=dict(mapping.get("metadata") or {}),
                usage=usage,
            )
        if kind == OutcomeKind.WAITING.value:
            return StepOutcome.waiting(
                str(mapping.get("question") or ""),
                dependency=str(mapping.get("dependency") or "user_input"),
                usage=usage,
                **dict(mapping.get("metadata") or {}),
            )
        if kind == OutcomeKind.BLOCKED.value:
            return StepOutcome.blocked(
                str(mapping.get("dependency") or "provider_blocked"),
                text=str(mapping.get("assistant_text") or mapping.get("content") or ""),
                usage=usage,
                **dict(mapping.get("metadata") or {}),
            )
        return StepOutcome.final(
            str(mapping.get("assistant_text") or mapping.get("content") or ""),
            usage=usage,
            **dict(mapping.get("metadata") or {}),
        )

    @classmethod
    def _native_tool_calls(cls, message: Mapping[str, Any]) -> tuple[ToolCall, ...]:
        raw = message.get("tool_calls")
        if isinstance(raw, Mapping):
            raw = (raw,)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return tuple(ToolCall.from_provider(cls._as_mapping(item), index) for index, item in enumerate(raw))
        legacy = message.get("function_call")
        if isinstance(legacy, Mapping):
            return (
                ToolCall.from_provider(
                    {
                        "id": str(message.get("tool_call_id") or "legacy_call_0"),
                        "type": "function",
                        "function": dict(legacy),
                    },
                    0,
                ),
            )
        return ()

    @classmethod
    def _message_mapping(cls, response: Mapping[str, Any]) -> Mapping[str, Any]:
        choices = response.get("choices")
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, bytearray)) and choices:
            first = cls._as_mapping(choices[0])
            message = first.get("message")
            if isinstance(message, Mapping):
                return message
            delta = first.get("delta")
            if isinstance(delta, Mapping):
                return delta
        message = response.get("message")
        if isinstance(message, Mapping):
            return message
        return response

    @classmethod
    def _finish_reason(cls, response: Mapping[str, Any]) -> str:
        choices = response.get("choices")
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, bytearray)) and choices:
            return str(cls._as_mapping(choices[0]).get("finish_reason") or "")
        return str(response.get("finish_reason") or "")

    @staticmethod
    def _content_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
            return ""
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, Mapping):
                    text = text.get("value")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(item.get("content"), str):
                    parts.append(str(item["content"]))
        return "".join(parts)

    @staticmethod
    def _as_mapping(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if value is None:
            return {}
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            result = dump()
            if isinstance(result, Mapping):
                return result
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            result = to_dict()
            if isinstance(result, Mapping):
                return result
        data = getattr(value, "__dict__", None)
        return data if isinstance(data, Mapping) else {}


__all__ = [
    "ProviderAdapterConfig",
    "ProviderModelAdapter",
    "ProviderProtocolError",
]
