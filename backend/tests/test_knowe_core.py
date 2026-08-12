# knowe v0.6 — Harness 核心引擎
"""
test_knowe_core.py — 地基的单元测试。

一律用 httpx.MockTransport 造假模型：**不烧一分钱 key，跑的却是真的代码路径**
（真的 ProviderClient、真的 SSE 解析、真的 StreamAssembler、真的 AgentLoop）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from knowe_core import (
    AgentLoop, AgentLoopConfig, ProviderClient, StreamAssembler, ToolRegistry,
    ToolNotFoundError, ProviderAuthError, ProviderConnectionError,
    estimate_tokens, sanitize_messages, truncate_messages,
)


# ═══════════════════════════════════════════════════════════════
# 假模型
# ═══════════════════════════════════════════════════════════════

def sse(*deltas: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps({'choices': [{'delta': d}]})}" for d in deltas]
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode()


def text_stream(text: str, chunk: int = 3) -> bytes:
    return sse(*[{"content": text[i:i + chunk]} for i in range(0, len(text), chunk)])


def tool_stream(name: str, args: dict[str, Any], call_id: str = "call_1") -> bytes:
    """★ arguments 切成 5 字一片 —— 真实 DeepSeek 就是这么发的。"""
    raw = json.dumps(args, ensure_ascii=False)
    frags: list[dict[str, Any]] = [{
        "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": ""},
        }],
    }]
    for i in range(0, len(raw), 5):
        frags.append({"tool_calls": [{"index": 0, "function": {"arguments": raw[i:i + 5]}}]})
    return sse(*frags)


class FakeProvider:
    """按剧本应答。整数 = 返回那个 HTTP 状态码。"""

    def __init__(self, script: list[bytes | int]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if not self.script:
            return httpx.Response(200, content=text_stream("（剧本演完了）"))
        step = self.script.pop(0)
        if isinstance(step, int):
            return httpx.Response(step, content=b'{"error":"boom"}')
        return httpx.Response(200, content=step)


def client_for(script: list[bytes | int]) -> tuple[ProviderClient, FakeProvider]:
    fake = FakeProvider(script)
    return ProviderClient(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        client_factory=fake.factory,
    ), fake


# ═══════════════════════════════════════════════════════════════
# 一、stream_assembler —— 全项目 bug 密度最高的一段
# ═══════════════════════════════════════════════════════════════

def test_assembler_joins_text_deltas():
    a = StreamAssembler()
    for t in ["你", "好", "世界"]:
        a.feed({"type": "delta", "content": t})
    assert a.finalize()["content"] == "你好世界"


def test_assembler_reassembles_fragmented_tool_arguments():
    """★ arguments 是一个字一个字来的，id/name 只在第一片有。"""
    a = StreamAssembler()
    a.feed({"type": "tool_call", "tool_call": {
        "index": 0, "id": "call_9", "function": {"name": "propose_agents", "arguments": ""},
    }})
    for frag in ['{"age', 'nts":[', '{"id":"fe', '_1","role"', ':"前端"}]}']:
        a.feed({"type": "tool_call", "tool_call": {
            "index": 0, "function": {"arguments": frag},
        }})

    msg = a.finalize()
    tc = msg["tool_calls"][0]
    assert tc["id"] == "call_9"
    assert tc["function"]["name"] == "propose_agents"
    assert json.loads(tc["function"]["arguments"]) == {
        "agents": [{"id": "fe_1", "role": "前端"}],
    }


def test_assembler_handles_two_tool_calls_by_index():
    a = StreamAssembler()
    for idx, name in ((0, "a_tool"), (1, "b_tool")):
        a.feed({"type": "tool_call", "tool_call": {
            "index": idx, "id": f"c{idx}",
            "function": {"name": name, "arguments": "{}"},
        }})

    names = [tc["function"]["name"] for tc in a.finalize()["tool_calls"]]
    assert names == ["a_tool", "b_tool"]


def test_assembler_fires_callbacks_once_per_tool_name():
    seen: list[str] = []
    starts: list[int] = []
    a = StreamAssembler(
        tool_gen_callback=seen.append,
        tool_start_callback=lambda: starts.append(1),
    )
    for _ in range(3):     # 同一个工具名来三次
        a.feed({"type": "tool_call", "tool_call": {
            "index": 0, "id": "c0", "function": {"name": "x", "arguments": ""},
        }})

    assert seen == ["x"], "同一个工具名只该通报一次"
    assert starts == [1], "tool_start 只该响一次"


def test_assembler_quarantines_broken_json_arguments():
    """流被掐断时不崩溃，也不把畸形调用交给工具执行器。"""
    a = StreamAssembler()
    a.feed({"type": "tool_call", "tool_call": {
        "index": 0, "id": "c0", "function": {"name": "x", "arguments": '{"a":'},
    }})

    turn = a.finalize_turn()
    assert turn.kind == "protocol_error"
    assert "malformed JSON" in turn.error
    assert not turn.tool_calls


# ═══════════════════════════════════════════════════════════════
# 二、messages —— 孤儿 tool 结果 / token / 截断
# ═══════════════════════════════════════════════════════════════

def test_sanitize_injects_stub_for_orphan_tool_call():
    """
    ★ 有 tool_call 没有 tool 结果 → OpenAI 直接 400。
      必须补一个 stub，不然整段对话都发不出去。
    """
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [
            {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
        ]},
    ]
    out = sanitize_messages(msgs)

    assert out[-1]["role"] == "tool"
    assert out[-1]["tool_call_id"] == "call_1"
    assert len(msgs) == 2, "不能改原列表"


def test_sanitize_leaves_matched_pairs_alone():
    msgs = [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "x"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert sanitize_messages(msgs) == msgs


def test_estimate_tokens_counts_chinese_denser_than_english():
    """中文 1.5 字/token，英文 4 字/token —— 同样长度，中文更贵。"""
    assert estimate_tokens("中" * 30) > estimate_tokens("a" * 30)
    assert estimate_tokens("") == 0


def test_legacy_truncate_entry_point_no_longer_drops_history():
    msgs = [{"role": "system", "content": "你是总管"}]
    msgs += [{"role": "user", "content": "很长的一句话" * 50} for _ in range(20)]

    out = truncate_messages(msgs, max_tokens=200)

    assert out == msgs
    assert out is not msgs


def test_truncate_empty_is_empty():
    assert truncate_messages([]) == []


# ═══════════════════════════════════════════════════════════════
# 三、tool_registry
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_registry_register_and_execute_async_handler():
    reg = ToolRegistry()

    async def handler(args: dict[str, Any], **kw: Any) -> str:
        await asyncio.sleep(0)                 # ★ 真的 await（工具要等闸门）
        return f"ok:{args['x']}:{kw.get('agent_id')}"

    reg.register("t", "desc", {"type": "object"}, handler)

    assert await reg.execute("t", {"x": 1}, agent_id="fe_1") == "ok:1:fe_1"
    assert len(reg) == 1
    assert "t" in reg


@pytest.mark.asyncio
async def test_registry_accepts_sync_handlers_too():
    reg = ToolRegistry()
    reg.register("s", "d", {}, lambda args, **kw: "sync-ok")
    assert await reg.execute("s", {}) == "sync-ok"


@pytest.mark.asyncio
async def test_registry_unknown_tool_raises():
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await reg.execute("nope", {})


def test_registry_duplicate_name_is_rejected():
    """静默覆盖一个工具是灾难——宁可当场炸。"""
    reg = ToolRegistry()
    reg.register("t", "d", {}, lambda a, **k: "")
    with pytest.raises(ValueError):
        reg.register("t", "d2", {}, lambda a, **k: "")


def test_registry_schemas_are_openai_shaped():
    reg = ToolRegistry()
    reg.register("t", "干点什么", {"type": "object", "properties": {}},
                 lambda a, **k: "", requires_approval=True)

    s = reg.get_schemas()[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "t"
    assert s["function"]["description"] == "干点什么"
    assert reg.is_gated("t") is True


# ═══════════════════════════════════════════════════════════════
# 四、provider_client
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("base,expected", [
    ("https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
    ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
    ("https://x.com/v1/chat/completions", "https://x.com/v1/chat/completions"),
    ("https://api.deepseek.com/", "https://api.deepseek.com/chat/completions"),
])
def test_endpoint_autodetect(base: str, expected: str):
    c = ProviderClient(base_url=base, api_key="k")
    assert c._endpoint == expected


@pytest.mark.asyncio
async def test_chat_stream_yields_delta_and_finish():
    client, _ = client_for([text_stream("你好")])
    events = [e async for e in client.chat_stream([{"role": "user", "content": "hi"}])]

    kinds = [e["type"] for e in events]
    assert "delta" in kinds
    assert kinds[-1] == "finish"
    assert "".join(e["content"] for e in events if e["type"] == "delta") == "你好"


@pytest.mark.asyncio
async def test_chat_stream_sends_tools_and_temperature():
    client, fake = client_for([text_stream("嗯")])
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]

    async for _ in client.chat_stream([{"role": "user", "content": "hi"}],
                                      tools=tools, temperature=0.5):
        pass

    body = fake.requests[0]
    assert body["stream"] is True
    assert body["temperature"] == 0.5
    assert body["tools"] == tools


@pytest.mark.asyncio
async def test_401_is_auth_error_and_is_not_retried():
    client, fake = client_for([401, 401])
    with pytest.raises(ProviderAuthError):
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    assert len(fake.requests) == 1, "认证失败重试一百次也还是失败——不该重试"


@pytest.mark.asyncio
async def test_500_is_connection_error():
    client, _ = client_for([500, 500, 500])
    with pytest.raises(ProviderConnectionError) as e:
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    assert "500" in str(e.value)


@pytest.mark.asyncio
async def test_sse_split_across_chunk_boundaries_still_parses():
    """
    ★ chunk 边界不保证落在行边界上 —— 一条 JSON 可能被劈成两半。
      （所以 provider_client 自己攒 buffer，而不是图省事用 aiter_lines）
    """
    payload = text_stream("你好世界")

    def handler(_req: httpx.Request) -> httpx.Response:
        # 每次只吐 7 个字节，故意把行切碎
        async def gen():
            for i in range(0, len(payload), 7):
                yield payload[i:i + 7]
        return httpx.Response(200, content=gen())

    client = ProviderClient(
        base_url="https://x.com", api_key="k",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    text = "".join([
        e["content"] async for e in client.chat_stream([{"role": "user", "content": "hi"}])
        if e["type"] == "delta"
    ])
    assert text == "你好世界"


# ═══════════════════════════════════════════════════════════════
# 五、agent_loop
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_loop_basic_conversation_no_tools():
    client, _ = client_for([text_stream("你好，我是总管。")])
    loop = AgentLoop(client, ToolRegistry())

    result = await loop.run(AgentLoopConfig(system_prompt="你是总管"))

    assert result.final_response == "你好，我是总管。"
    assert result.iterations == 1
    assert result.tool_calls == []
    assert not result.is_interrupted


@pytest.mark.asyncio
async def test_loop_executes_tool_then_continues():
    client, fake = client_for([
        tool_stream("add", {"a": 1, "b": 2}),
        text_stream("答案是 3。"),
    ])
    reg = ToolRegistry()
    called: list[dict[str, Any]] = []

    async def add(args: dict[str, Any], **kw: Any) -> str:
        called.append(args)
        return json.dumps({"result": args["a"] + args["b"]})

    reg.register("add", "加法", {"type": "object"}, add)
    loop = AgentLoop(client, reg, tool_context={"agent_id": "coordinator"})

    result = await loop.run(AgentLoopConfig(messages=[{"role": "user", "content": "1+2"}]))

    assert called == [{"a": 1, "b": 2}]                 # 工具真的被调了，参数拼对了
    assert result.final_response == "答案是 3。"
    assert result.iterations == 2                        # 调完工具又转了一轮
    assert result.tool_calls[0]["name"] == "add"

    # ★ 工具结果被回填进 messages，模型第二轮才看得见
    tool_msgs = [m for m in fake.requests[1]["messages"] if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["result"] == 3


@pytest.mark.asyncio
async def test_loop_tool_error_is_fed_back_not_raised():
    """工具炸了 → 把错误说回给模型，让它自己改。**不能让引擎倒下。**"""
    client, fake = client_for([
        tool_stream("boom", {}),
        text_stream("那我换个做法。"),
    ])
    reg = ToolRegistry()

    async def boom(args: dict[str, Any], **kw: Any) -> str:
        raise RuntimeError("我炸了")

    reg.register("boom", "会炸", {}, boom)

    result = await loop_run(client, reg)

    assert result.final_response == "那我换个做法。"
    tool_msg = [m for m in fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert "我炸了" in tool_msg["content"]


async def loop_run(client: ProviderClient, reg: ToolRegistry):
    return await AgentLoop(client, reg).run(
        AgentLoopConfig(messages=[{"role": "user", "content": "干活"}]))


@pytest.mark.asyncio
async def test_loop_provider_error_becomes_result_error_not_exception():
    client, _ = client_for([500, 500, 500])
    result = await AgentLoop(client, ToolRegistry()).run(
        AgentLoopConfig(messages=[{"role": "user", "content": "hi"}]))

    assert result.error and "500" in result.error     # 不抛异常，装进 result
    assert not result.is_interrupted


@pytest.mark.asyncio
async def test_interrupt_mid_stream_stops_before_running_tools():
    """
    ★ 流到一半被打断（用户拒了提案 → 引擎打断总管）：
      **不许再去执行工具**。否则用户明明拒了，agent 还是把活干了。
    """
    client, fake = client_for([
        tool_stream("t", {}),          # 模型这一轮想调工具
        text_stream("不该跑到这里"),
    ])
    reg = ToolRegistry()
    called: list[int] = []
    reg.register("t", "t", {}, lambda a, **k: called.append(1) or "ok")

    loop = AgentLoop(client, reg)
    # 第一个 delta 一到就打断 —— 确定性地命中"流中途"这个时机
    loop._stream_delta_cb = lambda _t: loop.interrupt()
    # 这个剧本里没有 content delta，改用 tool_gen 回调来触发
    loop._tool_gen_cb = lambda _n: loop.interrupt()

    result = await loop.run(AgentLoopConfig(messages=[{"role": "user", "content": "hi"}]))

    assert result.is_interrupted
    assert called == [], "★ 被打断了就不能再执行工具"
    assert len(fake.requests) == 1, "也不该再去问模型第二轮"


@pytest.mark.asyncio
async def test_interrupt_before_run_is_cleared_by_design():
    """
    ⚠ 这条记录的是一个**刻意保留的 v0.1 行为**（不是 bug，但很容易踩）：

      AgentLoop.run() 开头会 clear() 中断标志——所以「run 之前调 interrupt()」
      会被抹掉。真正处理这种情况的是上一层的 KnoweAgent（它有 _interrupt_pending）。

      移植时保持一致，不擅自"改进"。但写在这儿，免得下一个人踩。
    """
    client, fake = client_for([text_stream("我还是说话了")])
    loop = AgentLoop(client, ToolRegistry())
    loop.interrupt()

    result = await loop.run(AgentLoopConfig(messages=[{"role": "user", "content": "hi"}]))

    assert not result.is_interrupted          # ← 被 clear 掉了
    assert len(fake.requests) == 1
    # 想要"跑之前就打断"的语义 → 用 KnoweAgent.interrupt()（见 test_harness_engine.py）


@pytest.mark.asyncio
async def test_many_tool_iterations_continue_until_provider_finishes():
    rounds = 10
    client, fake = client_for(
        [tool_stream("t", {}, call_id=f"c{index}") for index in range(rounds)]
        + [text_stream("完成")]
    )
    reg = ToolRegistry()
    reg.register("t", "t", {}, lambda a, **k: "ok")

    result = await AgentLoop(client, reg).run(
        AgentLoopConfig(messages=[{"role": "user", "content": "干活"}])
    )

    assert result.iterations == rounds + 1
    assert result.error is None
    assert result.final_response == "完成"
    assert len(fake.requests) == rounds + 1


@pytest.mark.asyncio
async def test_system_prompt_and_injected_messages_land_in_the_right_places():
    client, fake = client_for([text_stream("好")])
    await AgentLoop(client, ToolRegistry()).run(AgentLoopConfig(
        system_prompt="你是总管",
        messages=[
            {"role": "user", "content": "历史消息"},
            {"role": "user", "content": "引擎注入的指令"},
        ],
    ))

    msgs = fake.requests[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "你是总管"}
    assert msgs[1]["content"] == "历史消息"
    assert msgs[-1]["content"] == "引擎注入的指令"      # 引擎注入消息在最后
