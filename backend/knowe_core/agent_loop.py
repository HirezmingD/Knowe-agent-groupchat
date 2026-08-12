"""Coordinator Agent loop.

The loop is a carrier for provider messages and tool results.  It stops only when the
provider returns a final text turn, the user interrupts it, or a mechanical/protocol
error makes the current turn impossible to continue.  It deliberately has no model-turn
ceiling, token-estimate trimming, semantic "action announcement" detector, nudge budget,
or tool-call budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
from typing import Any

from knowe_core.errors import ToolExecutionError
from knowe_core.messages import sanitize_messages
from knowe_core.provider_client import ProviderClient
from knowe_core.stream_assembler import StreamAssembler, StreamDeltaCB, ToolGenCB, VoidCB
from knowe_core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentLoopConfig:
    """Configuration for one Coordinator run.

    Context sizing belongs to the context projection layer.  AgentLoop therefore accepts
    the already projected message sequence and does not apply another budget or FIFO trim.
    """

    def __init__(
        self,
        system_prompt: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.messages: list[dict[str, Any]] = list(messages) if messages else []


class AgentLoopResult:
    """Result of one Coordinator run."""

    def __init__(self) -> None:
        self.final_response: str = ""
        self.reasoning: str = ""  # [v1.0.23.3] 完整推理文本（最后文本回合的）
        self.reasoning_seconds: float = 0.0  # [v1.0.23.3]
        self.tool_calls: list[dict[str, Any]] = []
        self.iterations: int = 0
        self.is_interrupted: bool = False
        # A provider frame with neither text nor tool_calls is malformed transport data,
        # not an LLM answer.  It is not persisted into history.
        self.empty_turn: bool = False
        self.error: str | None = None
        self.messages: list[dict[str, Any]] = []
        self.tool_calls_executed: int = 0
        self.steers_injected: int = 0
        # Authoritative assistant/tool frames produced in this run.  The adapter appends
        # these to durable raw history; projected context is never written back here.
        self.new_messages: list[dict[str, Any]] = []


class AgentLoop:
    """Carry provider turns and execute typed tool calls until a semantic final turn."""

    def __init__(
        self,
        client: ProviderClient,
        registry: ToolRegistry,
        stream_delta_callback: StreamDeltaCB | None = None,
        reasoning_delta_callback: StreamDeltaCB | None = None,  # [v1.0.23.3]
        tool_gen_callback: ToolGenCB | None = None,
        tool_start_callback: VoidCB | None = None,
        tool_complete_callback: VoidCB | None = None,
        tool_context: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._stream_delta_cb = stream_delta_callback
        self._reasoning_delta_cb = reasoning_delta_callback  # [v1.0.23.3]
        self._tool_gen_cb = tool_gen_callback
        self._tool_start_cb = tool_start_callback
        self._tool_complete_cb = tool_complete_callback
        self._tool_context = dict(tool_context or {})
        self._interrupt_event = asyncio.Event()
        self._pending_steers: list[str] = []

    def interrupt(self) -> None:
        """Stop the current run without judging the model's work."""

        self._interrupt_event.set()
        logger.debug("AgentLoop: interrupt requested")

    def steer(self, text: str) -> None:
        """Insert an explicit harness note before the provider's next turn."""

        self._pending_steers.append(str(text))

    async def run(self, config: AgentLoopConfig) -> AgentLoopResult:
        result = AgentLoopResult()
        self._interrupt_event.clear()
        # Steers are run-local.  A stale note from an abandoned run must not leak into a
        # later user turn.
        self._pending_steers.clear()

        messages = self._build_initial_messages(config)
        last_content = ""

        while True:
            result.iterations += 1
            if self._interrupt_event.is_set():
                result.is_interrupted = True
                result.final_response = last_content
                result.messages = messages
                return result

            if self._pending_steers:
                for note in self._pending_steers:
                    messages.append({"role": "user", "content": note})
                result.steers_injected += len(self._pending_steers)
                self._pending_steers.clear()

            # Structural sanitation only: repair orphaned protocol frames.  This does not
            # estimate tokens, drop old facts, or rewrite ordinary language.
            request_messages = sanitize_messages(messages)
            if not request_messages:
                result.error = "No valid messages are available for the provider request."
                result.messages = messages
                return result

            tools = self._registry.get_schemas() if len(self._registry) > 0 else None
            assembler = StreamAssembler(
                stream_delta_callback=self._stream_delta_cb,
                reasoning_delta_callback=self._reasoning_delta_cb,  # [v1.0.23.3]
                tool_gen_callback=self._tool_gen_cb,
                tool_start_callback=self._tool_start_cb,
                tool_complete_callback=self._tool_complete_cb,
                tool_schemas=tools,
                # With tools present, text is held until the completed frame is classified
                # so partial control markup can never become public speech.
                tool_protocol_mode="normalize" if tools else "off",
            )

            t0 = time.monotonic()  # [v1.0.23.3] 本回合思考耗时起点

            stream = self._client.chat_stream(
                messages=request_messages,
                tools=tools,
                extra_body={"tool_choice": "auto"} if tools else None,
            )
            try:
                while True:
                    next_task = asyncio.ensure_future(stream.__anext__())
                    interrupt_task = asyncio.ensure_future(self._interrupt_event.wait())
                    try:
                        done, _pending = await asyncio.wait(
                            {next_task, interrupt_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except asyncio.CancelledError:
                        next_task.cancel()
                        interrupt_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await stream.aclose()
                        raise

                    if next_task not in done:
                        next_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await next_task
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await stream.aclose()
                        result.is_interrupted = True
                        break

                    interrupt_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await interrupt_task
                    try:
                        event = next_task.result()
                    except StopAsyncIteration:
                        break
                    assembler.feed(event)
                    if self._interrupt_event.is_set():
                        result.is_interrupted = True
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await stream.aclose()
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Coordinator provider call failed on turn %d", result.iterations)
                result.error = str(exc)
                result.final_response = last_content
                result.messages = messages
                return result

            # A half-generated tool frame cannot safely be committed or executed.
            if result.is_interrupted and tools:
                result.final_response = last_content
                result.messages = messages
                return result

            assembled = assembler.finalize_turn()
            # [v1.0.23.3] 每轮记录推理（后续文本回合会覆盖；最终以最后一次为准）
            if assembled.reasoning:
                result.reasoning = assembled.reasoning
                result.reasoning_seconds = round(time.monotonic() - t0, 1)
            if assembled.kind in {"protocol_error", "stream_error"}:
                logger.error(
                    "provider turn rejected by protocol gate: kind=%s encoding=%s reason=%s",
                    assembled.kind,
                    assembled.protocol_encoding,
                    assembled.error,
                )
                result.error = "模型返回了无法安全处理的工具调用协议，本轮未展示。"
                result.final_response = last_content
                result.messages = messages
                return result

            if self._interrupt_event.is_set():
                result.is_interrupted = True
                result.final_response = last_content
                result.messages = messages
                return result

            assistant_msg = assembled.message
            content = assembled.content or ""
            tool_calls = list(assembled.tool_calls)
            if not content.strip() and not tool_calls:
                result.empty_turn = True
                result.final_response = last_content
                result.messages = messages
                return result

            messages.append(assistant_msg)
            result.new_messages.append(copy.deepcopy(assistant_msg))
            if content:
                last_content = content

            if result.is_interrupted:
                result.final_response = content or last_content
                result.messages = messages
                return result

            if not tool_calls:
                # A plain provider answer is authoritative.  No regex decides that it
                # "sounds unfinished", and no hidden retry changes its wording.
                result.final_response = content
                result.messages = messages
                return result

            execution: list[tuple[dict[str, Any], str]] = []
            for tool_call in tool_calls:
                tool_name = str((tool_call.get("function") or {}).get("name") or "")
                try:
                    args = json.loads((tool_call.get("function") or {}).get("arguments") or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except (json.JSONDecodeError, TypeError, ValueError):
                    args = {}

                record = {"name": tool_name, "args": args, "result": ""}
                result.tool_calls.append(record)
                result.tool_calls_executed += 1
                try:
                    tool_result = await self._registry.execute(
                        tool_name,
                        args,
                        **self._tool_context,
                    )
                except ToolExecutionError as exc:
                    tool_result = json.dumps(
                        {"status": "error", "message": str(exc)},
                        ensure_ascii=False,
                    )
                    logger.error("tool execution failed: %s", exc)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    tool_result = json.dumps(
                        {"status": "error", "message": f"意外错误：{exc}"},
                        ensure_ascii=False,
                    )
                    logger.exception("tool %s failed unexpectedly", tool_name)
                record["result"] = tool_result
                execution.append((tool_call, tool_result))

            # Every provider tool_call_id receives exactly one tool response, including
            # errors.  This is protocol integrity, not a judgment about whether to retry.
            for tool_call, tool_result in execution:
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_result,
                }
                messages.append(tool_msg)
                result.new_messages.append(copy.deepcopy(tool_msg))

    @staticmethod
    def _build_initial_messages(config: AgentLoopConfig) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if config.system_prompt:
            messages.append({"role": "system", "content": config.system_prompt})
        messages.extend(copy.deepcopy(config.messages))
        return messages


__all__ = ["AgentLoop", "AgentLoopConfig", "AgentLoopResult"]
