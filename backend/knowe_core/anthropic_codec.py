# knowe — Anthropic Messages API 编解码
"""ProviderClient 的 anthropic_messages 传输边界编解码。

内部消息格式沿用 OpenAI 结构（作为协议无关载体）：role=system/user/assistant/tool，
assistant 带 ``tool_calls``，tool 带 ``tool_call_id``，工具 schema 用
``{type:function, function:{name, parameters}}``。本模块负责在传输边界把它翻译成
Anthropic Messages API 的线上格式（出站 encode），并把 Anthropic 的响应/SSE 翻译回
内部中立事件流（入站 decode）。引擎核心（agent_loop / StreamAssembler / messages /
context_compressor / 历史持久化）保持内部格式不变，零改动。

Anthropic API 差异要点（本模块专门处理）：
- 端点 ``/v1/messages``；认证 ``x-api-key`` + ``anthropic-version``（非 Bearer）
- ``system`` 是顶层参数，不能混在 messages 里（且可多段文本）
- ``tool_use`` 是 assistant 消息里的 content block，input 是**完整 JSON 对象**
- ``tool_result`` 挂在 **user** 角色下（OpenAI 是独立 tool 角色）
- 工具 schema 用 ``input_schema``，不是 OpenAI 的 ``parameters``
- ``max_tokens`` 必填（内部不加时给默认）
- SSE 事件格式完全不同（message_start / content_block_* / message_delta / message_stop），
  且工具 input 是 ``input_json_delta`` 增量拼成的完整 JSON
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Anthropic 要求的协议版本头
ANTHROPIC_VERSION = "2023-06-01"

#: anthropic 必填 max_tokens；内部不传时用的默认（生产 Worker 路径由 provider 自行决定
#: 输出长度，translate 只补一个保险值）。
DEFAULT_MAX_TOKENS = 4096


def resolve_endpoint(base_url: str) -> str:
    """从 core_base_url 的保护形态（``{base}/chat/completions#``）还原 Anthropic 端点。

    engine 传给 ProviderClient 的 base_url 已经被 ``core_base_url`` 换算成
    ``{base}/chat/completions#``（URL fragment 保护）。Anthropic 分支必须剥掉
    ``/chat/completions`` 与 ``#`` 再拼 ``/v1/messages``，否则会打出
    ``.../anthropic/chat/completions/v1/messages``。

    对直接传 ``{base}/v1/messages``（已含 /v1/messages）的情况幂等保留。
    """
    base = (base_url or "").split("#", 1)[0].rstrip("/")
    base = base[:-len("/chat/completions")] if base.endswith("/chat/completions") else base
    base = base.rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    return f"{base}/v1/messages"


def build_headers(api_key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Anthropic 请求头（x-api-key 而非 Bearer）。"""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "x-api-key": api_key or "",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if extra:
        headers.update(extra)
    return headers


def _content_text_list(text: Any) -> list[dict[str, Any]]:
    """把内部 content 字符串转成 Anthropic text block 列表。"""
    text = str(text or "")
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _tool_use_blocks(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI tool_calls → Anthropic tool_use content blocks（input 是完整 JSON 对象）。"""
    blocks: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        raw_args = fn.get("arguments") or "{}"
        if isinstance(raw_args, dict):
            payload: Any = raw_args
        else:
            try:
                payload = json.loads(str(raw_args))
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        blocks.append({
            "type": "tool_use",
            "id": str(call.get("id") or f"call_{len(blocks)}"),
            "name": name,
            "input": payload,
        })
    return blocks


def _tool_result_block(message: dict[str, Any]) -> dict[str, Any]:
    """内部 tool 消息 → Anthropic tool_result block（挂 user 角色下）。"""
    tool_use_id = str(message.get("tool_call_id") or "")
    content = message.get("content") or ""
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(content)}


def encode_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    system: str | None = None,
) -> dict[str, Any]:
    """OpenAI 内部分页 → Anthropic Messages API 请求体。

    - ``system`` 为显式外部注入时优先，否则从 messages 里抽 role=system 合并
    - assistant 的 tool_calls 转成 tool_use blocks（input 完整 JSON）
    - tool 转成 user 角色下的 tool_result blocks
    - tools（OpenAI schema）转 anthropic input_schema
    """
    if not messages:
        raise ValueError("anthropic_codec: messages must not be empty")

    system_texts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            c = str(m.get("content") or "")
            if c:
                system_texts.append(c)
        else:
            rest.append(m)

    # 显式 system 覆盖（优先）
    if system is not None:
        system_blocks: list[dict[str, Any]] = _content_text_list(system)
    else:
        sys_text = "\n".join(system_texts)
        system_blocks = _content_text_list(sys_text)

    api_messages: list[dict[str, Any]] = []
    for m in rest:
        role = m.get("role")
        if role == "user":
            content = m.get("content")
            if isinstance(content, list):  # 已是 block 形态
                api_messages.append({"role": "user", "content": content})
            else:
                blocks = _content_text_list(content)
                if blocks:
                    api_messages.append({"role": "user", "content": blocks})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            c = m.get("content")
            if c:
                blocks.extend(_content_text_list(c))
            tc = m.get("tool_calls")
            if isinstance(tc, list):
                blocks.extend(_tool_use_blocks(tc))
            if blocks:
                api_messages.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            result = _tool_result_block(m)
            # 连续 tool 合并进同一条 user（anthropic 允许一条 user 多个 block）
            if api_messages and api_messages[-1].get("role") == "user" and _is_pure_tool_user(api_messages[-1]):
                api_messages[-1]["content"].append(result)
            else:
                api_messages.append({"role": "user", "content": [result]})
        # 未知 role：跳过（不伪造语义）

    body: dict[str, Any] = {"model": model, "messages": api_messages}
    if system_blocks:
        body["system"] = system_blocks
    body["max_tokens"] = max_tokens if max_tokens and int(max_tokens) > 0 else DEFAULT_MAX_TOKENS
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = [_openai_tool_to_anthropic(t) for t in tools]
    return body


def _is_pure_tool_user(message: dict[str, Any]) -> bool:
    """判断某条 user 消息是否仅由 tool_result 组成（用于合并连续 tool 结果）。"""
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _openai_tool_to_anthropic(tool: dict[str, Any]) -> dict[str, Any]:
    """OpenAI function schema → Anthropic tool 定义（parameters → input_schema）。"""
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    name = str(fn.get("name") or "")
    params = fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}}
    desc = fn.get("description")
    out: dict[str, Any] = {"name": name, "input_schema": params}
    if desc:
        out["description"] = str(desc)
    return out


# ═══════════════════════════════════════════════════════════════
# 入站：Anthropic 响应 / SSE → 中立事件流
# ═══════════════════════════════════════════════════════════════

def decode_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Anthropic 非流式 JSON 响应 → 中立事件流。

    一条响应可能同时含 text 与 tool_use 块：text → delta，tool_use → tool_call。
    """
    events: list[dict[str, Any]] = []
    content = data.get("content")
    if isinstance(content, list):
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            text_value = block.get("text")
            if btype == "text" and text_value:
                events.append({"type": "delta", "content": str(text_value)})
            elif btype == "tool_use":
                events.append({
                    "type": "tool_call",
                    "tool_call": {
                        "index": index,
                        "id": str(block.get("id") or f"call_{index}"),
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                        },
                    },
                })
    if data.get("stop_reason"):
        events.append({"type": "finish", "reason": str(data["stop_reason"])})
    usage = data.get("usage")
    if isinstance(usage, dict) and usage:
        events.append({"type": "usage", "usage": usage})
    return events


class AnthropicStreamDecoder:
    """Anthropic SSE 流 → 中立事件。

    tool_use 的 input 是 ``input_json_delta`` 增量，必须按 content_block_index 累积，
    直到对应 ``content_block_stop`` 才发完整的 tool_call（arguments = 完整 JSON 串）。
    """

    def __init__(self) -> None:
        # content_block_index -> 累积 input 字符串
        self._tool_inputs: dict[int, str] = {}
        # content_block_index -> 是否 tool_use 块（区分 text/tool_use）
        self._tool_blocks: dict[int, bool] = {}
        # content_block_index -> tool_use 的 id/name（在 content_block_start 捕获）
        self._tool_meta: dict[int, dict[str, Any]] = {}
        self._text_index: int | None = None

    def feed(self, event_name: str, data: dict[str, Any]) -> list[dict[str, Any]]:
        """处理一个 Anthropic SSE 事件，返回（可能为空）中立事件列表。"""
        events: list[dict[str, Any]] = []
        if event_name == "message_start":
            # usage 在 message_start 自带；末尾 message_delta 也有。预留。
            pass
        elif event_name == "content_block_start":
            index = data.get("index")
            block = data.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._tool_blocks[index] = True
                self._tool_inputs[index] = ""
                self._tool_meta[index] = {
                    "id": str(block.get("id") or f"call_{index}"),
                    "name": str(block.get("name") or ""),
                }
            else:
                self._tool_blocks[index] = False
        elif event_name == "content_block_delta":
            delta = data.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                events.append({"type": "delta", "content": str(delta.get("text") or "")})
            elif dtype == "thinking_delta":
                events.append({"type": "reasoning_delta", "content": str(delta.get("thinking") or "")})
            elif dtype == "input_json_delta":
                index = data.get("index")
                cur = self._tool_inputs.get(index, "")
                self._tool_inputs[index] = cur + str(delta.get("partial_json") or "")
            elif dtype == "signature_delta":
                pass  # 思考签名，忽略
        elif event_name == "content_block_stop":
            index = data.get("index")
            if self._tool_blocks.get(index):
                meta = self._tool_meta.get(index, {})
                raw = self._tool_inputs.get(index, "")
                # input 完整 JSON 已是可序列化对象；转成 arguments JSON 串
                try:
                    payload = json.loads(raw) if raw.strip() else {}
                    args_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                except (json.JSONDecodeError, TypeError, ValueError):
                    args_str = raw  # 保真传回，交由 StreamAssembler 协议闸处理
                events.append({
                    "type": "tool_call",
                    "tool_call": {
                        "index": index,
                        "id": meta.get("id", f"call_{index}"),
                        "function": {"name": meta.get("name", ""), "arguments": args_str},
                    },
                })
            else:
                # text 块结束：无额外事件（delta 已在增量处理）
                pass
        elif event_name == "message_delta":
            delta = data.get("delta") or {}
            stop_reason = delta.get("stop_reason")
            if stop_reason:
                events.append({"type": "finish", "reason": str(stop_reason)})
            usage = data.get("usage")
            if isinstance(usage, dict) and usage:
                events.append({"type": "usage", "usage": usage})
        elif event_name == "message_stop":
            pass  # 流结束信号，无额外事件
        return events

    def final_events(self) -> list[dict[str, Any]]:
        """流结束时补齐未收尾的 finish（若 provider 没发 terminal stop_reason）。"""
        # Anthropic 正常流会在 message_delta 发 stop_reason；这里兜底不发，避免重复 finish。
        return []
