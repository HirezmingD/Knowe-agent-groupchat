"""
test_deepseek.py — 真 LLM 档的测试。

不碰网络、不花钱：用 httpx.MockTransport 造一个假的 DeepSeek，
吐真格式的 SSE（含分片的 tool_calls），走的是 deepseek.py 的真代码路径。

重点盯三件事：
  1. 全链路：模型提议 → 弹卡 → 用户点头 → agents_created / instruction_injected / report_submitted
  2. **人说了算**：用户拒绝/超时，模型只能收到一条 tool 消息，不能强行往下走
  3. **引擎不倒**：HTTP 500、参数传歪、流中途炸——全变成 error 事件，不抛异常
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.agents.base import Turn
from backend.agents.deepseek import (
    DeepSeekAgent, TOOLS, _merge_tool_call, _parse_members, ToolArgError,
)
from backend.config import CONFIG
from backend.gate import Gate
from backend.hub import Hub


# ═══════════════════════════════════════════════════════════════
# 假 DeepSeek：吐真格式的 SSE
# ═══════════════════════════════════════════════════════════════

def sse(*deltas: dict[str, Any]) -> bytes:
    """把若干 delta 拼成一段 SSE 流（末尾 [DONE]）。"""
    lines = []
    for d in deltas:
        lines.append(f"data: {json.dumps({'choices': [{'delta': d}]})}")
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode()


def text_stream(text: str, chunk: int = 3) -> bytes:
    return sse(*[{"content": text[i:i + chunk]} for i in range(0, len(text), chunk)])


def tool_stream(name: str, args: dict[str, Any], call_id: str = "call_1") -> bytes:
    """
    工具调用的流。**故意把 arguments 切成分片**——真实的 DeepSeek 就是这么发的，
    id/name 只在第一片有，arguments 一个字一个字拼。这正是最容易写错的地方。
    """
    raw = json.dumps(args, ensure_ascii=False)
    frags: list[dict[str, Any]] = [{
        "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": ""},
        }],
    }]
    for i in range(0, len(raw), 5):
        frags.append({
            "tool_calls": [{"index": 0, "function": {"arguments": raw[i:i + 5]}}],
        })
    return sse(*frags)


class FakeDeepSeek:
    """按脚本依次应答。每次 POST 消耗一条剧本。"""

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
        if isinstance(step, int):                    # 用整数表示「返回这个 HTTP 错误码」
            return httpx.Response(step, content=b'{"error":"boom"}')
        return httpx.Response(200, content=step)


# ═══════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════

class Harness:
    def __init__(self, script: list[bytes | int]) -> None:
        self.fake = FakeDeepSeek(script)
        self.hub = Hub()
        self.gate = Gate(self.hub, "p1")
        self.agent = DeepSeekAgent(client_factory=self.fake.factory)

    async def emit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.hub.emit("p1", payload)

    async def run(self, content: str = "做个官网") -> None:
        turn = Turn("p1", "项目一", content, [])
        await self.agent.run_turn(turn, self.emit, self.gate)

    # ⚠ 事件要从 hub 的 ring 里读，不能只收 agent 的 emit：
    #   审批卡和 approval_resolved 是 gate 直接经 hub 发的，不经过 agent。
    #   只盯 agent 那一个出口，就会漏掉一半的事件（这个夹具第一版就栽在这）。
    @property
    def events(self) -> list[dict[str, Any]]:  # type: ignore[override]
        return self.hub.projects["p1"].ring.events() if "p1" in self.hub.projects else []

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of(self, etype: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == etype]


@pytest.fixture(autouse=True)
def _fake_key():
    """
    给 CONFIG 塞个假 key（不塞的话代码会走「未配置」分支）。
    顺便把审批超时从 300 秒压到 2 秒——不压的话，任何一张没被点的卡
    都会把测试挂死五分钟。
    """
    object.__setattr__(CONFIG, "deepseek_api_key", "sk-test")
    object.__setattr__(CONFIG, "approval_timeout_s", 2.0)
    yield
    object.__setattr__(CONFIG, "deepseek_api_key", "")
    object.__setattr__(CONFIG, "approval_timeout_s", 300.0)


async def auto_approve(gate: Gate, decision: str = "approved", n: int = 1) -> None:
    """
    扮演屏幕前那个人：卡一出来就点。

    ⚠ 必须看 resolve() 的返回值：一张卡刚被点完、propose 还没醒过来把它 pop 掉的时候，
      它仍然躺在 _pending 里。不看返回值就会把同一张卡"点"两次，白白用掉一轮——
      于是下一张卡没人点，超时。（这个夹具第一版就是这么把全链路测试搞挂的。）
    """
    for _ in range(n):
        for _ in range(400):
            done = False
            for card_id in list(gate._pending):   # noqa: SLF001 — 测试里看内部状态是合理的
                if gate.resolve(card_id, decision):  # type: ignore[arg-type]
                    done = True
                    break
            if done:
                break
            await asyncio.sleep(0.005)


# ═══════════════════════════════════════════════════════════════
# 一、普通对话（不带工具）必须还是流式的
# ═══════════════════════════════════════════════════════════════

async def test_plain_chat_still_streams():
    h = Harness([text_stream("你好，我是总管。")])
    await h.run("你好")

    assert h.types()[0] == "agent_thinking"
    assert h.of("stream_delta"), "普通对话必须流式，不能一整坨吐出来"
    msg = h.of("message")[-1]
    assert msg["content"] == "你好，我是总管。"
    # 流式增量拼起来 == 完整消息
    assert "".join(e["content"] for e in h.of("stream_delta")) == msg["content"]
    assert not h.of("approval_card"), "没提议工具就不该有审批卡"


async def test_tools_are_sent_to_the_model():
    h = Harness([text_stream("嗯")])
    await h.run()

    body = h.fake.requests[0]
    names = [t["function"]["name"] for t in body["tools"]]
    assert names == ["propose_agents", "propose_next", "propose_remove_agent"]
    assert body["stream"] is True
    assert body["tool_choice"] == "auto"


# ═══════════════════════════════════════════════════════════════
# 二、全链路：提议 → 弹卡 → 点头 → 干活 → 交报告
# ═══════════════════════════════════════════════════════════════

async def test_full_chain_approve_everything():
    h = Harness([
        tool_stream("propose_agents", {"proposed": [{"id": "fe_1", "role": "前端"}]}),
        tool_stream("propose_next", {"target_id": "fe_1", "instruction": "写首页"}, "call_2"),
        text_stream("这是 fe_1 交的方案：先做栅格。"),   # 成员干活（子调用）
        text_stream("方案已经交上来了，你看看。"),        # 总管收口
    ])

    approver = asyncio.create_task(auto_approve(h.gate, "approved", n=2))
    await h.run()
    await approver

    t = h.types()
    assert "approval_card" in t
    assert t.index("approval_card") < t.index("agents_created"), "★ 必须先弹卡、后组队"
    assert "instruction_injected" in t
    assert "report_submitted" in t

    created = h.of("agents_created")[0]
    assert created["members"] == [{"id": "fe_1", "role": "前端"}]

    injected = h.of("instruction_injected")[0]
    assert injected["target_id"] == "fe_1"

    report = h.of("report_submitted")[0]
    assert report["agent_id"] == "fe_1"          # 报告是成员交的，不是总管
    assert len(report["report_hash"]) == 16

    # 成员干活时也是流式的（用户看得见他在写）
    assert any(e["agent_id"] == "fe_1" for e in h.of("stream_delta"))

    # 两张卡各有一条 resolution，一条不多
    assert len(h.of("approval_resolved")) == 2


async def test_approval_result_is_fed_back_to_the_model():
    """模型必须收到一条 tool 消息告诉它结果——不然它下一轮不知道发生了什么。"""
    h = Harness([
        tool_stream("propose_agents", {"proposed": [{"id": "fe_1", "role": "前端"}]}),
        text_stream("好的。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "approved"))
    await h.run()
    await approver

    second = h.fake.requests[1]["messages"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert "approved" in tool_msgs[0]["content"]
    # assistant 那条带 tool_calls 的消息也要在上下文里（不然模型不认得自己说过什么）
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in second)


# ═══════════════════════════════════════════════════════════════
# 三、★ 人说了算：拒绝 / 超时，模型不能硬来
# ═══════════════════════════════════════════════════════════════

async def test_rejection_stops_the_chain_and_model_explains():
    h = Harness([
        tool_stream("propose_agents", {"proposed": [{"id": "fe_1", "role": "前端"}]}),
        text_stream("好吧，你拒绝了组队，那我自己来。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "rejected"))
    await h.run()
    await approver

    assert not h.of("agents_created"), "★ 用户拒绝了，就绝不能有人入驻"
    assert h.of("approval_resolved")[0]["resolution"] == "rejected"

    # 模型收到了 "rejected"，并且用它圆了场
    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "rejected"
    assert "拒绝" in h.of("message")[-1]["content"]


async def test_timeout_is_fed_back_as_timeout():
    h = Harness([
        tool_stream("propose_agents", {"proposed": [{"id": "fe_1", "role": "前端"}]}),
        text_stream("等太久了，先不组队。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "timeout"))
    await h.run()
    await approver

    assert not h.of("agents_created")
    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "timeout"


async def test_propose_next_before_team_exists_is_refused_not_crashed():
    """★ 模型想跳过组队直接派活 → 告诉它「先组队」，不崩溃、不弹卡。"""
    h = Harness([
        tool_stream("propose_next", {"target_id": "fe_1", "instruction": "干活"}),
        text_stream("好，我先组队。"),
    ])
    await h.run()

    assert not h.of("approval_card"), "队都没组，不该弹派活卡"
    assert not h.of("error"), "这不是错误，是把话说回给模型"

    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert "先调 propose_agents" in tool_msg["content"]


async def test_propose_next_to_a_stranger_is_refused():
    h = Harness([
        tool_stream("propose_agents", {"proposed": [{"id": "fe_1", "role": "前端"}]}),
        tool_stream("propose_next", {"target_id": "be_9", "instruction": "干活"}, "call_2"),
        text_stream("我搞错人了。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "approved"))
    await h.run()
    await approver

    assert len(h.of("approval_card")) == 1, "只该有组队那一张卡"
    assert not h.of("instruction_injected")

    tool_msgs = [m for m in h.fake.requests[2]["messages"] if m["role"] == "tool"]
    assert "不在团队里" in tool_msgs[-1]["content"]


# ═══════════════════════════════════════════════════════════════
# 四、★ 铁律：任何异常都变 error 事件，引擎不倒
# ═══════════════════════════════════════════════════════════════

async def test_http_500_becomes_error_event_not_exception():
    h = Harness([500])
    await h.run()                              # 不抛异常，这本身就是断言

    err = h.of("error")
    assert len(err) == 1
    assert "500" in err[0]["message"]


async def test_network_failure_becomes_error_event():
    def boom() -> httpx.AsyncClient:
        def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("连不上")
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    hub = Hub()
    agent = DeepSeekAgent(client_factory=boom)
    events: list[dict[str, Any]] = []

    async def emit(p: dict[str, Any]) -> dict[str, Any]:
        ev = await hub.emit("p1", p)
        events.append(ev)
        return ev

    await agent.run_turn(Turn("p1", "项目一", "你好", []), emit, Gate(hub, "p1"))

    assert [e["type"] for e in events if e["type"] == "error"], "网络断了必须出 error 事件"


@pytest.mark.parametrize("bad_args", [
    {"proposed": "fe_1"},                            # 传成字符串
    {"proposed": []},                                # 空数组
    {"proposed": [{"name": "小前"}]},                # 字段名不对
    {"proposed": [{"id": "fe_1"}]},                  # 缺 role
    {},                                              # 什么都没传
])
async def test_malformed_tool_args_never_crash(bad_args):
    """★ 模型把参数传歪 → 把话说回给它，绝不崩溃、绝不弹畸形卡。"""
    h = Harness([
        tool_stream("propose_agents", bad_args),
        text_stream("我重新提一次。"),
    ])
    await h.run()                                   # 不抛异常

    assert not h.of("approval_card"), "参数不合法就不该弹卡"
    assert not h.of("agents_created")

    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith("error:")  # 模型能看懂，会自己改


async def test_broken_json_arguments_are_quarantined_without_partial_display():
    """Malformed streamed JSON is a protocol failure, not a retryable business tool error.

    The current protocol gate must isolate the whole turn before an approval card or
    partial natural-language response becomes visible.  A later user turn can retry from
    clean history; the Harness must not feed a fabricated tool result back into the same
    malformed provider turn.
    """
    frag = sse({
        "tool_calls": [{
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": "propose_agents", "arguments": '{"proposed": [{'},  # 截断
        }],
    })
    h = Harness([frag, text_stream("不应在本轮消费")])
    await h.run()

    assert len(h.fake.requests) == 1
    assert not h.of("approval_card")
    assert not h.of("agents_created")
    assert not h.of("stream_delta")
    assert not h.of("message")
    errors = h.of("error")
    assert len(errors) == 1
    assert "无法安全处理的工具调用协议" in errors[0]["message"]


async def test_unknown_tool_name_is_quarantined_by_protocol_gate():
    h = Harness([
        tool_stream("rm_rf_slash", {"path": "/"}),
        text_stream("不应在本轮消费"),
    ])
    await h.run()

    assert len(h.fake.requests) == 1
    assert not h.of("approval_card")
    assert not h.of("message")
    errors = h.of("error")
    assert len(errors) == 1
    assert "无法安全处理的工具调用协议" in errors[0]["message"]


async def test_many_tool_rounds_continue_until_model_text():
    rounds = 10
    h = Harness([
        *[
            tool_stream(
                "propose_agents",
                {"proposed": [{"id": f"fe_{index}", "role": "前端"}]},
                call_id=f"call_{index}",
            )
            for index in range(rounds)
        ],
        text_stream("十轮提案都已处理。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "approved", n=rounds))
    await h.run()
    await approver

    assert not h.of("error")
    assert h.of("message")[-1]["content"] == "十轮提案都已处理。"
    assert len(h.fake.requests) == rounds + 1


async def test_no_api_key_yields_error_event():
    object.__setattr__(CONFIG, "deepseek_api_key", "")
    hub = Hub()
    agent = DeepSeekAgent()                        # 不注入 factory → 走真实分支
    events: list[dict[str, Any]] = []

    async def emit(p: dict[str, Any]) -> dict[str, Any]:
        ev = await hub.emit("p1", p)
        events.append(ev)
        return ev

    await agent.run_turn(Turn("p1", "项目一", "你好", []), emit, Gate(hub, "p1"))

    err = [e for e in events if e["type"] == "error"]
    assert err and "DEEPSEEK_API_KEY" in err[0]["message"]


# ═══════════════════════════════════════════════════════════════
# 五、分片合并 / 参数校验（纯函数）
# ═══════════════════════════════════════════════════════════════

def test_tool_call_fragments_merge_correctly():
    """id/name 只在第一片，arguments 一片一片拼——拼错了整个 function calling 就废了。"""
    pending: dict[int, dict[str, Any]] = {}
    _merge_tool_call(pending, {
        "index": 0, "id": "c1", "function": {"name": "propose_agents", "arguments": ""},
    })
    _merge_tool_call(pending, {"index": 0, "function": {"arguments": '{"pro'}})
    _merge_tool_call(pending, {"index": 0, "function": {"arguments": 'posed": []}'}})

    assert pending[0]["id"] == "c1"
    assert pending[0]["function"]["name"] == "propose_agents"
    assert json.loads(pending[0]["function"]["arguments"]) == {"proposed": []}


def test_parallel_tool_calls_are_kept_apart():
    pending: dict[int, dict[str, Any]] = {}
    _merge_tool_call(pending, {"index": 0, "id": "a", "function": {"name": "x", "arguments": "1"}})
    _merge_tool_call(pending, {"index": 1, "id": "b", "function": {"name": "y", "arguments": "2"}})

    assert pending[0]["id"] == "a" and pending[1]["id"] == "b"


def test_parse_members_dedupes():
    members = _parse_members([
        {"id": "fe_1", "role": "前端"},
        {"id": "fe_1", "role": "前端"},        # 模型偶尔会报两遍同一个人
        {"id": "be_1", "role": "后端"},
    ])
    assert [m["id"] for m in members] == ["fe_1", "be_1"]


def test_parse_members_has_no_semantic_team_size_cap():
    members = _parse_members([{"id": f"fe_{i}", "role": "前端"} for i in range(12)])
    assert len(members) == 12
    assert members[-1]["id"] == "fe_11"


def test_tool_schema_matches_gate_contract():
    """工具的参数名必须和 gate.propose 的 card_body 对得上——对不上就是发畸形卡。"""
    by_name = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}

    assert list(by_name["propose_agents"]["properties"]) == ["proposed"]
    assert set(by_name["propose_next"]["properties"]) == {"target_id", "instruction"}
