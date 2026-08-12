# knowe v0.6 — Harness 核心引擎
"""
knowe_core — 自研 Agent 核心层。

它是 Harness 的地基，**不认识 Knowe 的任何东西**（不知道 hub、gate、project）：
只知道「怎么和一个 OpenAI 兼容的模型对话，怎么调工具，怎么被打断」。
Knowe 的业务（闸门、审批卡、事件广播）全都在 backend/ 那一层，靠注入进来。

  provider_client  —— 说 HTTP：SSE 流式 + 重试退避
  stream_assembler —— 把分片的 SSE 攒成完整消息（tool_calls 的 arguments 是一个字一个字来的）
  tool_registry    —— 每引擎一个，无全局状态
  messages         —— 孤儿 tool 结果检测、token 估算、截断
  agent_loop       —— 循环：对话 ⇄ 工具，含 nudge-retry 与 interrupt
  errors           —— 类型化异常
"""

from knowe_core.agent_loop import AgentLoop, AgentLoopConfig, AgentLoopResult
from knowe_core.errors import (
    AgentError,
    AgentInterrupted,
    KnoweError,
    MaxIterationsExceeded,
    MessageError,
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    StreamError,
    StreamParseError,
    ToolExecutionError,
    ToolNotFoundError,
)
from knowe_core.messages import (
    estimate_message_tokens,
    estimate_tokens,
    sanitize_messages,
    truncate_messages,
)
from knowe_core.provider_client import ProviderClient
from knowe_core.stream_assembler import StreamAssembler
from knowe_core.tool_registry import ToolDef, ToolRegistry

__all__ = [
    "AgentLoop", "AgentLoopConfig", "AgentLoopResult",
    "ProviderClient", "StreamAssembler", "ToolRegistry", "ToolDef",
    "sanitize_messages", "truncate_messages", "estimate_tokens",
    "estimate_message_tokens",
    "KnoweError", "AgentError", "AgentInterrupted", "MaxIterationsExceeded",
    "MessageError", "ProviderError", "ProviderAuthError", "ProviderRateLimitError",
    "ProviderTimeoutError", "ProviderConnectionError", "ProviderBadResponseError",
    "StreamError", "StreamParseError", "ToolExecutionError", "ToolNotFoundError",
]
