# knowe — anthropic_codec 单元测试
"""anthropic_codec：OpenAI 内部分页 ↔ Anthropic Messages API 的双向编解码。

一律纯函数/纯解析，不烧 key。用 MockTransport 造假模型（与 test_knowe_core 同风格）。

关键覆盖：
- encode_request：system 抽离 / assistant tool_calls → tool_use / tool → user tool_result /
  tools parameters → input_schema / max_tokens 默认 / 显式 system 优先
- decode_response：text / tool_use 转中立事件
- AnthropicStreamDecoder：text_delta / thinking_delta / input_json_delta 累积 →
  block_stop → tool_call（完整 JSON） / stop_reason / usage
- resolve_endpoint / build_headers
"""

from __future__ import annotations

import json

import pytest

from knowe_core.anthropic_codec import (
    AnthropicStreamDecoder,
    build_headers,
    decode_response,
    encode_request,
    resolve_endpoint,
)


# ═══════════════════════════════════════════════════════════════
# 一、resolve_endpoint / build_headers
# ═══════════════════════════════════════════════════════════════

def test_resolve_endpoint_from_core_base_url_fragment():
    """engine 传入的是 core_base_url 保护形态（.../chat/completions#）→ 还原 /v1/messages。"""
    assert (
        resolve_endpoint("https://api.minimaxi.com/anthropic/chat/completions#")
        == "https://api.minimaxi.com/anthropic/v1/messages"
    )


def test_resolve_endpoint_plain_anthropic_base():
    assert resolve_endpoint("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"


def test_resolve_endpoint_trailing_slash():
    assert resolve_endpoint("https://api.minimaxi.com/anthropic/") == "https://api.minimaxi.com/anthropic/v1/messages"


def test_resolve_endpoint_idempotent_already_v1():
    assert (
        resolve_endpoint("https://api.anthropic.com/v1/messages")
        == "https://api.anthropic.com/v1/messages"
    )


def test_build_headers_uses_x_api_key_not_bearer():
    headers = build_headers("sk-123")
    assert headers["x-api-key"] == "sk-123"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


# ═══════════════════════════════════════════════════════════════
# 二、encode_request：出站
# ═══════════════════════════════════════════════════════════════

def test_encode_system_extracted_to_top_level():
    body = encode_request(
        model="MiniMax-M2.7",
        messages=[
            {"role": "system", "content": "你是项目经理"},
            {"role": "user", "content": "你好"},
        ],
    )
    assert body["model"] == "MiniMax-M2.7"
    assert body["system"] == [{"type": "text", "text": "你是项目经理"}]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "你好"}]}]


def test_encode_requires_max_tokens_default():
    body = encode_request(model="m", messages=[{"role": "user", "content": "hi"}])
    assert body["max_tokens"] == 4096


def test_encode_explicit_max_tokens_wins():
    body = encode_request(
        model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=100
    )
    assert body["max_tokens"] == 100


def test_encode_user_text_blocks():
    body = encode_request(model="m", messages=[{"role": "user", "content": "hello"}])
    assert body["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "hello"}]}


def test_encode_assistant_tool_calls_to_tool_use():
    body = encode_request(
        model="m",
        messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_9",
                        "type": "function",
                        "function": {"name": "propose_agents", "arguments": '{"agents":[]}'},
                    }
                ],
            }
        ],
    )
    msg = body["messages"][0]
    assert msg["role"] == "assistant"
    block = msg["content"][0]
    assert block["type"] == "tool_use"
    assert block["id"] == "call_9"
    assert block["name"] == "propose_agents"
    assert block["input"] == {"agents": []}


def test_encode_tool_result_under_user_role():
    body = encode_request(
        model="m",
        messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "成功"},
        ],
    )
    # tool → user 角色下 tool_result block
    last = body["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "call_1"
    assert last["content"][0]["content"] == "成功"


def test_encode_consecutive_tool_results_merged_into_one_user():
    body = encode_request(
        model="m",
        messages=[
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},
        ],
    )
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert len(body["messages"][0]["content"]) == 2
    assert body["messages"][0]["content"][1]["tool_use_id"] == "c2"


def test_encode_tools_parameters_to_input_schema():
    body = encode_request(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "搜索",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
            }
        ],
    )
    tool = body["tools"][0]
    assert tool["name"] == "search"
    assert tool["description"] == "搜索"
    assert tool["input_schema"]["type"] == "object"
    assert "q" in tool["input_schema"]["properties"]
    assert "parameters" not in tool


def test_encode_explicit_system_overrides():
    body = encode_request(
        model="m",
        messages=[{"role": "system", "content": "来自消息"}, {"role": "user", "content": "hi"}],
        system="显式覆盖",
    )
    assert body["system"] == [{"type": "text", "text": "显式覆盖"}]


# ═══════════════════════════════════════════════════════════════
# 三、decode_response：非流式
# ═══════════════════════════════════════════════════════════════

def test_decode_response_text_and_tool_use():
    events = decode_response(
        {
            "content": [
                {"type": "text", "text": "我来处理"},
                {"type": "tool_use", "id": "toolu_01", "name": "search", "input": {"q": "x"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    kinds = [e["type"] for e in events]
    assert kinds == ["delta", "tool_call", "finish", "usage"]
    assert events[0]["content"] == "我来处理"
    tc = events[1]["tool_call"]
    assert tc["function"]["name"] == "search"
    assert json.loads(tc["function"]["arguments"]) == {"q": "x"}
    assert events[2]["reason"] == "tool_use"


def test_decode_response_multi_blocks_index_increments():
    events = decode_response(
        {
            "content": [
                {"type": "tool_use", "id": "a", "name": "f1", "input": {}},
                {"type": "tool_use", "id": "b", "name": "f2", "input": {}},
            ],
            "stop_reason": "tool_use",
        }
    )
    tool_calls = [e["tool_call"] for e in events if e["type"] == "tool_call"]
    assert [tc["index"] for tc in tool_calls] == [0, 1]
    assert [tc["id"] for tc in tool_calls] == ["a", "b"]


# ═══════════════════════════════════════════════════════════════
# 四、AnthropicStreamDecoder：流式
# ═══════════════════════════════════════════════════════════════

def test_stream_decoder_text_delta():
    dec = AnthropicStreamDecoder()
    events = dec.feed("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "你好"}})
    assert events == [{"type": "delta", "content": "你好"}]


def test_stream_decoder_thinking_delta_to_reasoning():
    dec = AnthropicStreamDecoder()
    events = dec.feed("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "思考中"}})
    assert events == [{"type": "reasoning_delta", "content": "思考中"}]


def test_stream_decoder_tool_input_accumulates_then_fires_on_stop():
    """input_json_delta 按 index 累积，block_stop 才发完整 tool_call。"""
    dec = AnthropicStreamDecoder()
    # 开始 tool_use 块
    dec.feed("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "tu_1", "name": "search"}})
    # 增量 input
    assert dec.feed("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"q":'}}) == []
    assert dec.feed("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": ' "x"}'}}) == []
    # block 结束 → 完整 tool_call
    events = dec.feed("content_block_stop", {"index": 0})
    assert len(events) == 1
    tc = events[0]["tool_call"]
    assert tc["id"] == "tu_1"
    assert tc["function"]["name"] == "search"
    assert json.loads(tc["function"]["arguments"]) == {"q": "x"}


def test_stream_decoder_stop_reason_finish():
    dec = AnthropicStreamDecoder()
    events = dec.feed("message_delta", {"delta": {"stop_reason": "end_turn"}})
    assert events == [{"type": "finish", "reason": "end_turn"}]


def test_stream_decoder_usage_event():
    dec = AnthropicStreamDecoder()
    events = dec.feed("message_delta", {"usage": {"input_tokens": 3, "output_tokens": 2}})
    assert events == [{"type": "usage", "usage": {"input_tokens": 3, "output_tokens": 2}}]


def test_stream_decoder_text_then_tool_sequence():
    """text 块 + tool 块并存：text_delta 立即发，tool 累积到 stop。"""
    dec = AnthropicStreamDecoder()
    assert dec.feed("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "好的"}}) == [{"type": "delta", "content": "好的"}]
    dec.feed("content_block_start", {"index": 1, "content_block": {"type": "tool_use", "id": "tu", "name": "f"}})
    dec.feed("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}})
    events = dec.feed("content_block_stop", {"index": 1})
    assert len(events) == 1 and events[0]["type"] == "tool_call"
