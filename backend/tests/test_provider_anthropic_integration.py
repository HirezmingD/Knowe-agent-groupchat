# knowe — ProviderClient × anthropic transport 集成测试
"""验证 ProviderClient(transport="anthropic_messages") 走 codec 的正确性。

- 出站：请求打 /v1/messages + x-api-key + anthropic body
- 入站：Anthropic SSE → 中立事件流（与 openai 路径完全同构，可直接进 StreamAssembler）

复用 test_knowe_core 的 FakeProvider/MockTransport 风格，不烧 key。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from knowe_core import ProviderClient, StreamAssembler


def tool_use_stream(name: str, input_obj: dict, call_id: str = "tu_1") -> bytes:
    """构造一段 Anthropic SSE：text 增量 + tool_use input 增量 + finish。"""
    events = [
        {"type": "message_start", "message": {"id": "m_1", "usage": {"input_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "好的"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": call_id, "name": name}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": json.dumps(input_obj)}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    lines: list[str] = []
    for ev in events:
        lines.append("event: " + ev["type"])
        lines.append("data: " + json.dumps(ev, ensure_ascii=False))
        lines.append("")  # blank line closes frame
    return ("\n".join(lines)).encode()


def text_stream(text: str, chunk: int = 3) -> bytes:
    """纯文本 Anthropic SSE 流。"""
    lines: list[str] = []
    lines.append("event: message_start")
    lines.append("data: " + json.dumps({"type": "message_start", "message": {"id": "m_1"}}))
    lines.append("")
    for i in range(0, len(text), chunk):
        lines.append("event: content_block_delta")
        lines.append("data: " + json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + chunk]}}))
        lines.append("")
    lines.append("event: message_delta")
    lines.append("data: " + json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}))
    lines.append("")
    lines.append("event: message_stop")
    lines.append("data: " + json.dumps({"type": "message_stop"}))
    lines.append("")
    return ("\n".join(lines)).encode()


class FakeAnthropic:
    """按剧本应答：流式返回 SSE，非流式返回 JSON。记录收到的请求。"""

    def __init__(self, stream: bytes | None = None, json_response: dict | None = None) -> None:
        self.stream = stream
        self.json_response = json_response
        self.requests: list[dict] = []

    def factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": json.loads(request.content),
        })
        if self.json_response is not None:
            return httpx.Response(200, json=self.json_response)
        return httpx.Response(200, content=self.stream or b"", headers={"content-type": "text/event-stream"})


def make_client(stream: bytes | None = None, json_response: dict | None = None) -> tuple[ProviderClient, FakeAnthropic]:
    fake = FakeAnthropic(stream=stream, json_response=json_response)
    return (
        ProviderClient(
            base_url="https://api.minimaxi.com/anthropic/chat/completions#",  # core_base_url 保护形态
            api_key="sk-test",
            transport="anthropic_messages",
            client_factory=fake.factory,
        ),
        fake,
    )


@pytest.mark.asyncio
async def test_anthropic_chat_stream_endpoint_and_headers():
    client, fake = make_client(text_stream("你好"))
    events = [e async for e in client.chat_stream([{"role": "user", "content": "hi"}])]

    # 出站：endpoint 是 /v1/messages（剥掉 /chat/completions#）
    assert fake.requests[0]["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
    headers = fake.requests[0]["headers"]
    assert headers.get("x-api-key") == "sk-test"
    assert headers.get("anthropic-version") == "2023-06-01"
    assert "authorization" not in {k.lower() for k in headers}

    # 入站：中立事件流
    kinds = [e["type"] for e in events]
    assert "delta" in kinds
    text = "".join(e["content"] for e in events if e["type"] == "delta")
    assert text == "你好"
    assert kinds[-1] == "finish"


@pytest.mark.asyncio
async def test_anthropic_tool_stream_flow():
    client, fake = make_client(tool_use_stream("search", {"q": "x"}, "tu_1"))

    # 直接进 StreamAssembler，确认能被既有协议闸消费成 tool_call turn
    assembler = StreamAssembler()
    async for e in client.chat_stream(
        [{"role": "user", "content": "帮我搜"}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}],
        extra_body=None,
    ):
        assembler.feed(e)
        if e.get("type") == "finish":
            break

    # 出站 body：anthropic tool schema（input_schema）
    body = fake.requests[0]["body"]
    assert body["tools"][0]["name"] == "search"
    assert "input_schema" in body["tools"][0]
    assert "parameters" not in body["tools"][0]

    # 组装结果应是 tool_calls turn（完整 JSON arguments）
    turn = assembler.finalize_turn()
    assert turn.kind == "tool_calls"
    calls = turn.tool_calls
    assert len(calls) == 1
    fn = calls[0]["function"]
    assert fn["name"] == "search"
    assert json.loads(fn["arguments"]) == {"q": "x"}


@pytest.mark.asyncio
async def test_anthropic_body_message_encoding():
    client, fake = make_client(
        json_response={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    await client.chat(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
            {"role": "user", "content": "继续"},
        ]
    )
    body = fake.requests[0]["body"]
    # system 抽到顶层
    assert body["system"] == [{"type": "text", "text": "你是助手"}]
    # max_tokens 必填（默认）
    assert body["max_tokens"] == 4096
    # messages：user / assistant(tool_use) / user(tool_result) / user
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "user"]
    # assistant 的 tool_use
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][1]["content"][0]["id"] == "c1"
    # tool → user 下的 tool_result
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert body["messages"][2]["content"][0]["tool_use_id"] == "c1"


# ═══════════════════════════════════════════════════════════════
# 五、model_adapter.from_legacy 重建 client 时携带 transport（断点 4）
# ═══════════════════════════════════════════════════════════════


class _LegacySource:
    """模拟旧 Agent 对象：无 _client 时从 base_url/api_key/model 分支重建 client。"""

    def __init__(self, base_url, api_key, model, transport="openai_chat", client_factory=None):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.transport = transport
        self._client_factory = client_factory


def test_from_legacy_rebuilds_client_with_transport():
    """断点 4：from_legacy 的 legacy 重建分支必须携带 transport。"""
    from knowe_adapters.model_adapter import ProviderModelAdapter

    source = _LegacySource(
        base_url="https://api.minimaxi.com/anthropic",
        api_key="sk-x",
        model="MiniMax-M2.7",
        transport="anthropic_messages",
    )
    adapter = ProviderModelAdapter.from_legacy(source)
    client = adapter.client if hasattr(adapter, "client") else adapter._client  # 兼容内部名
    assert client.transport == "anthropic_messages"
    assert client._endpoint == "https://api.minimaxi.com/anthropic/v1/messages"


def test_from_legacy_defaults_openai_when_no_transport():
    """无 transport 信息的 legacy source 默认走 openai_chat（零回归）。"""
    from knowe_adapters.model_adapter import ProviderModelAdapter

    class Plain:
        base_url = "https://api.deepseek.com"
        api_key = "k"
        model = "deepseek-v4-flash"

    adapter = ProviderModelAdapter.from_legacy(Plain())
    client = adapter.client if hasattr(adapter, "client") else adapter._client
    assert client.transport == "openai_chat"
    assert client._endpoint == "https://api.deepseek.com/chat/completions"
