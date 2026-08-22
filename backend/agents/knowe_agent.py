"""Provider holder and Coordinator-only AgentLoop adapter.

Workers no longer own an AgentLoop in Phase F.  Their ProviderClient is retained on
this object solely so ``knowe_adapters.model_adapter.ProviderModelAdapter`` can issue
one provider request per WorkerRuntime step.  ``run_conversation`` is therefore a
Coordinator API and rejects every Worker identity.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable

from knowe_core.agent_loop import AgentLoop, AgentLoopConfig
from knowe_core.errors import ProviderError
from knowe_core.provider_client import ClientFactory, ProviderClient, build_http_timeout
from knowe_core.tool_registry import ToolRegistry

from ..config import CONFIG
from ..attachments import build_format_fallback, inject_into_last_user
from ..context_compressor import project_messages
from ..token_usage import TokenUsageCollector, extract_token_usage, extract_token_usage_parts
from knowe_core.provider_client import normalize_usage_buckets

log = logging.getLogger("knowe.agent")


def _context_usage_percent(projected_messages: list[dict[str, Any]]) -> float | None:
    """投影产物估算 token ÷ 模型窗口 × 100；数据不足/异常时返回 None（前端显示 --）。"""
    try:
        from ..context_compressor import _projected_token_estimate, _token_budget
        estimated = _projected_token_estimate(projected_messages)
        budget = _token_budget(None)
        if budget <= 0:
            return None
        return round(estimated / budget * 100, 2)
    except Exception:
        return None


def _history_key(message: dict[str, Any]) -> tuple[Any, ...]:
    role = message.get("role")
    if role == "tool":
        return ("tool", message.get("tool_call_id"), message.get("content"))
    calls = message.get("tool_calls")
    if role == "assistant" and calls:
        signature = tuple(
            (
                call.get("id"),
                (call.get("function") or {}).get("name"),
                (call.get("function") or {}).get("arguments"),
            )
            for call in calls
            if isinstance(call, dict)
        )
        return ("assistant", message.get("content"), signature)
    return (role, message.get("content"))


class _UsageTrackingClient:
    """Transparent provider proxy that captures one usage payload per API stream."""

    def __init__(self, delegate: ProviderClient, collector: TokenUsageCollector):
        self._delegate = delegate
        self._collector = collector

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def chat_stream(self, *args: Any, **kwargs: Any):
        usage_parts: dict[str, int] = {}
        raw_usage_frames: list[dict[str, Any]] = []
        try:
            async for event in self._delegate.chat_stream(*args, **kwargs):
                try:
                    if event.get("type") == "usage" and isinstance(event.get("usage"), dict):
                        # [M1] provider 已透传 usage 帧——保留原帧以便缓存三桶归一化。
                        raw_usage_frames.append(event["usage"])
                    else:
                        parts = extract_token_usage_parts(event)
                        if parts is not None:
                            usage_parts.update(parts)
                except Exception:
                    log.debug("provider usage parse failed", exc_info=True)
                yield event
        finally:
            try:
                if raw_usage_frames:
                    # [v1.0.34-实测v2] 流式网关（DeepSeek 等）每个 chunk 都带 usage 帧，
                    # 逐帧记账会把一次调用重复记 N 次（token jsonl 同秒多条相同记录）。
                    # 只取最后一帧（完整累计值），一次调用恰一条。
                    latest_frame = raw_usage_frames[-1]
                    buckets = normalize_usage_buckets(latest_frame)
                    if buckets is not None:
                        self._collector.add({
                            "input_tokens": buckets["cache_hit_input"] + buckets["cache_miss_input"],
                            "output_tokens": buckets["output"],
                            "prompt_cache_hit_tokens": buckets["cache_hit_input"],
                            "prompt_cache_miss_tokens": buckets["cache_miss_input"],
                        })
                        log.warning("[usage-debug] add via frames: n_frames=%d latest=%r", len(raw_usage_frames), buckets)
                else:
                    latest = extract_token_usage(usage_parts)
                    if latest is not None:
                        self._collector.add(latest)
                        log.warning("[usage-debug] add via fallback")
            except Exception:
                log.debug("provider usage collection failed", exc_info=True)


class ProviderConfig:
    """Provider configuration shared by Coordinator and the Worker model adapter.

    Agent effort is not represented by iteration or approximate-token ceilings.  Retry
    and network-progress policies are injected explicitly from the application config.
    """

    def __init__(
        self,
        provider: str = "",
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.7,
        max_retries: int | None = None,
        connect_timeout_s: float | None = None,
        read_timeout_s: float | None = None,
        write_timeout_s: float | None = None,
        pool_timeout_s: float | None = None,
        transport: str = "openai_chat",
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.transport = transport or "openai_chat"
        self.max_retries = (
            max(0, int(CONFIG.provider_max_retries))
            if max_retries is None else max(0, int(max_retries))
        )
        self.connect_timeout_s = (
            CONFIG.provider_connect_timeout_s
            if connect_timeout_s is None else connect_timeout_s
        )
        self.read_timeout_s = (
            CONFIG.provider_read_timeout_s if read_timeout_s is None else read_timeout_s
        )
        self.write_timeout_s = (
            CONFIG.provider_write_timeout_s
            if write_timeout_s is None else write_timeout_s
        )
        self.pool_timeout_s = (
            CONFIG.provider_pool_timeout_s if pool_timeout_s is None else pool_timeout_s
        )


class KnoweAgent:
    """Coordinator loop adapter plus a reusable ProviderClient boundary for Workers."""

    def __init__(
        self,
        agent_id: str,
        role: str,
        provider_config: ProviderConfig | None = None,
        tool_registry: ToolRegistry | None = None,
        client_factory: ClientFactory | None = None,
        stream_delta_callback: Callable[[str], Any] | None = None,
        reasoning_delta_callback: Callable[[str], Any] | None = None,  # [v1.0.23.3]
        tool_gen_callback: Callable[[str], Any] | None = None,
        tool_start_callback: Callable[[], Any] | None = None,
        tool_complete_callback: Callable[[], Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.role = role
        self.ephemeral_system_prompt = ""
        self.stream_delta_callback = stream_delta_callback
        self.reasoning_delta_callback = reasoning_delta_callback  # [v1.0.23.3]
        self.tool_gen_callback = tool_gen_callback
        self.tool_start_callback = tool_start_callback
        self.tool_complete_callback = tool_complete_callback

        cfg = provider_config or ProviderConfig()
        self._provider_cfg = cfg
        self._client = ProviderClient(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            model=cfg.model,
            timeout=build_http_timeout(
                connect=cfg.connect_timeout_s,
                read=cfg.read_timeout_s,
                write=cfg.write_timeout_s,
                pool=cfg.pool_timeout_s,
            ),
            max_retries=cfg.max_retries,
            client_factory=client_factory,
            provider=cfg.provider,
            transport=cfg.transport,
            # [v1.0.39.2] 附件格式降级：网关不认 file 块 → 回调换 text 块单次重发。
            #   Coordinator（run_conversation）与 Worker（model_adapter 借 _client）
            #   共用同一个 ProviderClient，此处挂一次两侧同效。
            on_format_rejected=build_format_fallback(
                cfg.provider, cfg.base_url, cfg.model,
            ),
        )
        self._tool_registry = tool_registry or ToolRegistry()
        # Only the Coordinator uses durable raw AgentLoop messages. Worker state is
        # TaskEnvelope/TaskRun/TaskJournal data owned by WorkerRuntime.
        self._history: list[dict[str, Any]] = []
        self._interrupt_pending = False
        self._loop: AgentLoop | None = None

    async def run_conversation(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
        *,
        system_prompt_override: str | None = None,
        registry_override: ToolRegistry | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run one Coordinator turn; Worker execution belongs to WorkerRuntime."""

        if self.agent_id != "coordinator":
            raise RuntimeError("Worker execution must enter through WorkerRuntime")

        collector = TokenUsageCollector()

        def attach_usage(payload: dict[str, Any]) -> dict[str, Any]:
            try:
                payload.update(collector.result_fields(self._provider_cfg.model))
            except Exception:
                log.debug("token usage aggregation failed", exc_info=True)
            return payload

        if self._interrupt_pending:
            self._interrupt_pending = False
            return attach_usage(
                {
                    "final_response": "",
                    "tool_calls": [],
                    "iterations": 0,
                    "is_interrupted": True,
                    "error": None,
                }
            )

        if conversation_history:
            seen = {_history_key(message) for message in self._history}
            for message in conversation_history:
                if not isinstance(message, dict):
                    continue
                key = _history_key(message)
                if key not in seen:
                    self._history.append(copy.deepcopy(message))
                    seen.add(key)
        self._history.append({"role": "user", "content": user_message})

        try:
            self._loop = AgentLoop(
                client=_UsageTrackingClient(self._client, collector),
                registry=registry_override or self._tool_registry,
                stream_delta_callback=self.stream_delta_callback,
                reasoning_delta_callback=self.reasoning_delta_callback,  # [v1.0.23.3]
                tool_gen_callback=self.tool_gen_callback,
                tool_start_callback=self.tool_start_callback,
                tool_complete_callback=self.tool_complete_callback,
                tool_context={"agent_id": self.agent_id},
            )
            # [v1.0.34] M3 查询感知投影：开关开时传当前用户消息做 BM25 优先保留；
            # 关时不传 query，行为与 v1.0.33 完全一致。
            projected_messages, projected_count = project_messages(
                self._history,
                query=user_message if CONFIG.query_aware else None,
            )
            # [v1.0.34-M4] 上下文占用百分比：投影产物估算 token ÷ 模型窗口。
            # 附在 result 私有字段，engine 落盘时消费（_persist_token_usage）。
            context_usage_pct = _context_usage_percent(projected_messages)
            # [v1.0.19.4] ★ 把本回合用户附件并进最后一条 user 消息（当前回合）。
            #   只改这份投影副本；self._history 保持纯文本，附件不落盘、不逐回合重发。
            inject_into_last_user(projected_messages, attachments)
            result = await self._loop.run(
                AgentLoopConfig(
                    system_prompt=(
                        system_prompt_override
                        if system_prompt_override is not None
                        else self.ephemeral_system_prompt
                    ),
                    messages=projected_messages,
                )
            )
            # Diagnostics only.  Raw history remains append-only and is never replaced by
            # the provider projection.
            setattr(result, "projected_message_count", projected_count)
            if not collector.has_calls:
                fallback = extract_token_usage(result)
                if fallback is not None:
                    collector.add(fallback)

            new_messages = getattr(result, "new_messages", None)
            if new_messages:
                self._history.extend(
                    copy.deepcopy(message)
                    for message in new_messages
                    if isinstance(message, dict)
                )
            else:
                for message in reversed(getattr(result, "messages", []) or []):
                    if isinstance(message, dict) and message.get("role") == "assistant" and message.get("content"):
                        self._history.append(copy.deepcopy(message))
                        break
            return attach_usage(
                {
                    "final_response": result.final_response or "",
                    "reasoning": getattr(result, "reasoning", "") or "",            # [v1.0.23.3]
                    "reasoning_seconds": getattr(result, "reasoning_seconds", 0.0),  # [v1.0.23.3]
                    "tool_calls": result.tool_calls,
                    "iterations": result.iterations,
                    "is_interrupted": result.is_interrupted,
                    "error": result.error,
                    "tool_calls_executed": getattr(result, "tool_calls_executed", 0),
                    "steers_injected": getattr(result, "steers_injected", 0),
                    "projected_message_count": getattr(result, "projected_message_count", 0),
                    "_context_usage_pct": context_usage_pct,  # [v1.0.34-M4] 私有遥测字段
                }
            )
        except ProviderError as exc:
            log.error("KnoweAgent[%s] provider error: %s", self.agent_id, exc)
            return attach_usage(
                {
                    "final_response": "",
                    "tool_calls": [],
                    "iterations": 1,
                    "is_interrupted": False,
                    "error": str(exc),
                }
            )
        except Exception as exc:
            log.exception("KnoweAgent[%s] unexpected error", self.agent_id)
            return attach_usage(
                {
                    "final_response": "",
                    "tool_calls": [],
                    "iterations": 1,
                    "is_interrupted": False,
                    "error": str(exc),
                }
            )
        finally:
            self._loop = None

    def interrupt(self) -> None:
        """Interrupt the Coordinator turn; Worker task cancellation is owned by Engine."""

        if self.agent_id != "coordinator":
            return
        if self._loop is not None:
            self._loop.interrupt()
        else:
            self._interrupt_pending = True

    @property
    def registry(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def history(self) -> list[dict[str, Any]]:
        if self.agent_id != "coordinator":
            return []
        return self._history

    def __repr__(self) -> str:
        return f"KnoweAgent({self.agent_id}, {self.role})"


__all__ = ["KnoweAgent", "ProviderConfig"]
