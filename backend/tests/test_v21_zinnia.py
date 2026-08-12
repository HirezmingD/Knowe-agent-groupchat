# knowe v0.21 — 测试：知知（问题三）
"""
投诉两条：

  ① 用户让知知在项目里搜个函数 → 她拿只读文件工具翻了几轮 →
     屏幕上弹出「知知的工具调用超过 4 轮，已中止（防止无限循环）」。
     用户问了个问题，软件回他一个**内部计数器**。
  ② 用户再问 → 「项目内的事务我不管，你去项目里跟总管说。」
     逻辑没错，但这是在赶人。

这里用一个假的流式客户端把整条路真跑一遍——不打网络，也不需要 key。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import zinnia as z                  # noqa: E402
from backend.agents.base import Turn                    # noqa: E402


# ═══════════ 假客户端：把 SSE 那一层照着真格式喂回去 ═══════════

class FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aiter_text(self):
        # ProviderClient now owns chunk-safe SSE parsing.  Feed it one realistic
        # text chunk with physical newlines rather than bypassing that parser via
        # the retired ``aiter_lines`` boundary.
        yield "\n".join(self._lines) + "\n"

    async def aread(self):
        return b""


class FakeClient:
    """记下每一次请求的 payload —— 收口轮到底有没有把工具收走，全看这个。"""

    def __init__(self, scripted: list[list[str]]) -> None:
        self.scripted = scripted
        self.calls: list[dict] = []
        self.is_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, headers=None, json=None, timeout=None, **_kwargs):
        self.calls.append(json or {})
        idx = min(len(self.calls) - 1, len(self.scripted) - 1)
        return FakeStream(self.scripted[idx])


def sse_text(text: str) -> list[str]:
    return [f'data: {json.dumps({"choices": [{"delta": {"content": text}}]})}', "data: [DONE]"]


def sse_tool(name: str, args: str = "{}") -> list[str]:
    frag = {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": "c1", "type": "function",
        "function": {"name": name, "arguments": args},
    }]}}]}
    return [f"data: {json.dumps(frag)}", "data: [DONE]"]


def sse_reply(text: str) -> list[str]:
    return sse_tool(z.PUBLIC_REPLY_TOOL, json.dumps({"content": text}, ensure_ascii=False))


def run(agent: z.ZinniaAgent, content: str = "帮我在项目里搜一下运行时交付状态"):
    events: list[dict] = []

    async def emit(payload):
        events.append(payload)
        return dict(payload)

    class FakeGate:
        async def propose(self, **kw):
            return "approved"

    turn = Turn("__platform__", "平台", content, [])
    asyncio.run(agent.run_turn(turn, emit, FakeGate()))
    return events


def agent_with(scripted: list[list[str]]) -> tuple[z.ZinniaAgent, FakeClient]:
    client = FakeClient(scripted)
    a = z.ZinniaAgent(client_factory=lambda: client,
                      harness_brief=lambda: "（没有项目）",
                      platform_brief=lambda: "Knowe v0.21")
    a.api_key = "test-key"
    return a, client


# ═══════════ ① v1.0.18：没有模型轮次裁判 ═══════════

def test_more_than_the_legacy_round_count_is_allowed() -> None:
    rounds = 8
    scripts = [sse_tool("list_dir", '{"path": "/tmp"}') for _ in range(rounds)]
    scripts.append(sse_reply("这些目录页已经看完了。"))
    agent, client = agent_with(scripts)

    events = run(agent)

    assert not [event for event in events if event["type"] == "error"]
    messages = [event for event in events if event["type"] == "message"]
    assert messages[-1]["content"] == "这些目录页已经看完了。"
    assert len(client.calls) == rounds + 1
    assert not hasattr(z, "MAX_ROUNDS")


def test_every_round_keeps_the_same_typed_tool_contract() -> None:
    rounds = 7
    agent, client = agent_with(
        [sse_tool("read_harness_memory") for _ in range(rounds)]
        + [sse_reply("查完了。")]
    )

    run(agent, "我有哪些项目")

    expected_names = [row["function"]["name"] for row in z._build_tools()]
    assert len(client.calls) == rounds + 1
    for call in client.calls:
        assert [row["function"]["name"] for row in call.get("tools") or []] == expected_names
        assert "tool_choice" not in call


def test_long_loop_keeps_all_tool_results_in_history() -> None:
    rounds = 8
    agent, client = agent_with(
        [sse_tool("read_harness_memory") for _ in range(rounds)]
        + [sse_reply("我已经把公告栏信息整理好了。")]
    )

    events = run(agent, "读一下平台动态")

    last_request = client.calls[-1]["messages"]
    assert len([message for message in last_request if message.get("role") == "tool"]) == rounds
    assert [event for event in events if event["type"] == "message"][-1]["content"] == "我已经把公告栏信息整理好了。"


def test_provider_failure_is_reported_without_a_hidden_wrapup_fallback() -> None:
    class Boom(FakeClient):
        def stream(self, method, url, headers=None, json=None, timeout=None, **_kwargs):
            self.calls.append(json or {})
            raise RuntimeError("网络炸了")

    client = Boom([])
    agent = z.ZinniaAgent(
        client_factory=lambda: client,
        harness_brief=lambda: "",
        platform_brief=lambda: "",
    )
    agent.api_key = "k"

    events = run(agent)

    assert [event for event in events if event["type"] == "error"]
    assert not [event for event in events if event["type"] == "message"]


def test_normal_turn_is_untouched() -> None:
    agent, client = agent_with([sse_reply("你好，想做点什么？")])
    events = run(agent, "你好")
    messages = [event for event in events if event["type"] == "message"]
    assert len(messages) == 1 and messages[0]["content"] == "你好，想做点什么？"
    assert len(client.calls) == 1


def test_tool_then_answer_is_untouched() -> None:
    agent, client = agent_with([sse_tool("read_harness_memory"), sse_reply("你有两个项目")])
    events = run(agent, "我有哪些项目")
    messages = [event for event in events if event["type"] == "message"]
    assert len(messages) == 1 and messages[0]["content"] == "你有两个项目"
    assert len(client.calls) == 2


# ═══════════ ② 引导要像前台，不像门卫 ═══════════

def test_prompt_teaches_the_boundary() -> None:
    """先分清项目事 / 平台事，再开口——分错了要么越权，要么把该接的球推出去。"""
    p = z._read_prompt_file("zinnia_system_prompt.md")
    assert "项目事还是平台事" in p
    assert "需要动手做，还是只需要知道" in p


def test_prompt_kills_the_cold_line() -> None:
    """
    ★ 老 prompt 里写着「用户问项目里的事，让他去项目里跟总管说」——
      于是她就照着说了：「项目内的事务我不管，你去项目里跟总管说。」
      模型很听话，问题是这话本身就凉。
    """
    p = z._read_prompt_file("zinnia_system_prompt.md")
    assert "让他去项目里跟总管说" not in p, "那句冷冰冰的原话还在，她会照着念"
    assert "像个好前台，不是像个门卫" in p
    assert "先接住" in p and "别重复推" in p


def test_prompt_shows_both_versions_side_by_side() -> None:
    """正反例并排摆着，比讲十句道理管用。"""
    p = z._read_prompt_file("zinnia_system_prompt.md")
    assert "项目内的事务我不管" in p                  # 反例（明确标 ✗）
    assert "✗" in p and "✓" in p


def test_prompt_stops_her_grepping_project_code() -> None:
    """
    ★ 她**有** read_file / list_dir，所以她会去试——然后翻好几轮翻不全，用户白等。
      那两把工具是用来回答平台问题的，不是当代码搜索用的。
    """
    p = z._read_prompt_file("zinnia_system_prompt.md")
    assert "别自己硬扛" in p
    assert "不是用来当代码搜索的" in p


def test_she_can_name_what_the_project_can_do() -> None:
    """
    「那边能直接搜代码、跑命令」比「这不归我管」有用一百倍。
    而且这份清单跟总管看的是同一个真源——她不会承诺项目里其实没有的能力。
    """
    block = z.ZinniaAgent._capabilities_block()
    assert "项目团队能做什么" in block
    assert "运行命令" in block and "开浏览器" in block


def test_capabilities_block_is_in_the_context() -> None:
    a, _ = agent_with([sse_text("hi")])
    ctx = a._context_block()
    assert "项目团队能做什么" in ctx
    # [v1.0.21.3] 上下文模板头已英文化（CONTEXT_TEMPLATE 固定英文头 + msg() 双语值）
    assert "Platform Updates" in ctx and "Platform Info" in ctx


def test_context_survives_a_broken_capabilities_module(monkeypatch) -> None:
    """★ 接待是用户见到的第一个人。一个上下文增强块出岔子，不能让她开不了口。"""
    import backend.capabilities as caps
    monkeypatch.setattr(caps, "worker_tool_names", lambda: (_ for _ in ()).throw(RuntimeError("炸")))
    a, _ = agent_with([sse_text("hi")])
    ctx = a._context_block()
    assert "Platform Updates" in ctx                      # 别的照常
    monkeypatch.undo()
