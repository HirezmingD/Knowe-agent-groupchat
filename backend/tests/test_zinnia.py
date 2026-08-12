"""
test_zinnia.py — 知知（平台级接待）。

和 DeepSeek 那组一样：不碰网络、不花钱，用 httpx.MockTransport 造假模型。

重点盯：
  1. 她能把项目**真的**建出来（不是嘴上说建好了）
  2. 她**不越界**——不组队、不派活，只有 create_project 一个工具
  3. `__platform__` 不是项目（不进 projects.json，不进左栏项目列表）
  4. 异常不倒引擎
"""

from __future__ import annotations

from typing import Any

import asyncio
import os
from unittest.mock import patch

import pytest

from backend.agents.base import Turn
from backend.agents.zinnia import (
    PLATFORM_PROJECT_ID, PUBLIC_REPLY_TOOL, ZINNIA, ZinniaAgent, _build_tools, _clean_name, new_project_id,
)
from backend.agents.deepseek import ToolArgError
from backend.config import CONFIG
from backend.gate import Gate
from backend.hub import Hub
from backend.server import KnoweServer

# 复用 DeepSeek 测试里那台假模型（同一套 SSE 格式，没必要写两遍）
from .test_deepseek import FakeDeepSeek, auto_approve, sse, tool_stream  # noqa: F401


def reply_stream(text: str) -> bytes:
    """Current Zinnia protocol publishes user-visible text via a typed tool."""
    return tool_stream(PUBLIC_REPLY_TOOL, {"content": text})


@pytest.fixture(autouse=True)
def _fake_key():
    previous_key = CONFIG.deepseek_api_key
    previous_timeout = CONFIG.approval_timeout_s
    object.__setattr__(CONFIG, "deepseek_api_key", "sk-test")
    # Individual approval tests use a finite deadline so a broken approver fails fast.
    object.__setattr__(CONFIG, "approval_timeout_s", 2.0)
    yield
    object.__setattr__(CONFIG, "deepseek_api_key", previous_key)
    object.__setattr__(CONFIG, "approval_timeout_s", previous_timeout)


class Harness:
    """知知 + 一个真 hub。create_project 记账，看看她到底建没建。"""

    def __init__(self, script: list[bytes | int]) -> None:
        self.fake = FakeDeepSeek(script)
        self.hub = Hub()
        self.gate = Gate(self.hub, PLATFORM_PROJECT_ID)
        self.created: list[tuple[str, str]] = []
        self.agent = ZinniaAgent(
            create_project=self._create,
            client_factory=self.fake.factory,
        )

    async def _create(self, name: str) -> tuple[str, str]:
        pid = new_project_id()
        self.created.append((pid, name))
        return pid, name

    async def emit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.hub.emit(PLATFORM_PROJECT_ID, payload)

    async def run(self, content: str = "我想做个网站") -> None:
        turn = Turn(PLATFORM_PROJECT_ID, "知知", content, [])
        await self.agent.run_turn(turn, self.emit, self.gate)

    @property
    def events(self) -> list[dict[str, Any]]:
        p = self.hub.projects.get(PLATFORM_PROJECT_ID)
        return p.ring.events() if p else []

    def of(self, etype: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == etype]


# ═══════════════════════════════════════════════════════════════
# 一、接待：先聊，不急着建
# ═══════════════════════════════════════════════════════════════

async def test_zinnia_chats_and_streams():
    h = Harness([reply_stream("你想做个什么样的网站？给谁用？")])
    await h.run()

    assert h.of("stream_delta"), "知知说话也要流式（用户看得见她在打字）"
    msg = h.of("message")[-1]
    assert msg["agent_id"] == ZINNIA
    assert msg["content"] == "你想做个什么样的网站？给谁用？"
    assert not h.created, "★ 光聊天不该建项目"


# ═══════════════════════════════════════════════════════════════
# 一·五、[v1.0.22.1] 终局语义：文本即回复，协议错误即收尾
# ═══════════════════════════════════════════════════════════════

async def test_plain_text_is_a_direct_reply():
    """★ [v1.0.22.1] 模型直接输出普通文本（如问候）→ 直接作为回复，一次调用结束，不死循环。

    这是 v1.0.22.1 死循环的回归测试：改造前 "你好" 会被协议闸门隔离并无限重试。
    """
    from .test_deepseek import text_stream
    h = Harness([text_stream("你好！有什么可以帮你？")])
    await h.run("你好")

    msg = h.of("message")[-1]
    assert msg["agent_id"] == ZINNIA
    assert msg["content"] == "你好！有什么可以帮你？"
    assert len(h.fake.requests) == 1, "★ 一次调用就该结束，不允许重试"
    assert not h.created


async def test_protocol_garbage_ends_with_wrapup_without_retry():
    """★ [v1.0.22.1] 协议杂讯（无法解码的工具帧）→ 人话收尾，终局，绝不重试。"""
    from .test_deepseek import sse
    garbage = sse({"content": '{"function": {"name": "read_file"}}'})   # 结构像帧但不可解码
    h = Harness([garbage])
    await h.run("你好")

    assert len(h.fake.requests) == 1, "★ 协议错误是终局，绝不重试"
    msg = h.of("message")[-1]
    assert msg["agent_id"] == ZINNIA
    assert "没处理明白" in msg["content"], "本地兜底文案（测试环境无辅助模型）"


async def test_zinnia_only_has_safe_platform_tools():
    """知知可读平台/网页并回复，但唯一有业务副作用的工具仍是建项目。"""
    names = [t["function"]["name"] for t in _build_tools()]
    assert names == [
        "create_project", "read_harness_memory", "read_file", "list_dir",
        "web_search", "web_extract", "browser_navigate", "browser_snapshot",
        "browser_click", "browser_scroll", "browser_close", PUBLIC_REPLY_TOOL,
    ]
    assert "propose_agents" not in names and "propose_next" not in names

    h = Harness([reply_stream("好")])
    await h.run()
    sent = [t["function"]["name"] for t in h.fake.requests[0]["tools"]]
    assert sent == names


async def test_zinnia_proposes_a_create_project_card():
    """★ [v0.5] 建群走审批卡了——知知只管提议，建不建、叫什么名，用户说了算。"""
    h = Harness([
        tool_stream("create_project", {"project_name": "官网改版"}),
        reply_stream("建好了，接下来交给总管。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "approved"))
    await h.run()
    await approver

    card = h.of("approval_card")[0]
    assert card["tool"] == "create_project"
    assert card["agent_id"] == ZINNIA
    assert card["card"]["project_name"] == "官网改版"     # 卡上带着待确认的项目名
    assert card["card"]["approval_id"] == card["card_id"]

    assert h.of("approval_resolved")[0]["resolution"] == "approved"


async def test_zinnia_does_not_create_before_approval():
    """★ 卡还挂着的时候，项目绝不能已经建出来了。"""
    h = Harness([
        tool_stream("create_project", {"project_name": "官网改版"}),
        reply_stream("好。"),
    ])
    rejecter = asyncio.create_task(auto_approve(h.gate, "rejected"))
    await h.run()
    await rejecter

    assert h.of("approval_resolved")[0]["resolution"] == "rejected"
    # 知知拿到 rejected，自己圆场；她本来也不负责真正建项目（那是 server 在 approve 时干的）
    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "rejected"


# ═══════════════════════════════════════════════════════════════
# 二、建项目：真的建出来
# ═══════════════════════════════════════════════════════════════

async def test_zinnia_gets_approved_and_wraps_up():
    h = Harness([
        tool_stream("create_project", {"project_name": "公司官网改版"}),
        reply_stream("项目已经建好了，左栏能看到。接下来总管接手。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "approved"))
    await h.run("就叫公司官网改版吧")
    await approver

    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith("approved")
    assert "总管" in h.of("message")[-1]["content"]


@pytest.mark.parametrize("bad", [
    {"project_name": ""},
    {"project_name": "   "},
    {"project_name": 123},
    {"project_name": "把这一整段需求当成项目名塞进来" * 5},   # 超长
    {},
])
async def test_malformed_project_name_never_crashes(bad):
    """★ 模型把名字传歪 → 把话说回给它，绝不崩溃、绝不弹出一张畸形卡。"""
    h = Harness([
        tool_stream("create_project", bad),
        reply_stream("我换个短点的名字。"),
    ])
    await h.run()

    assert not h.of("approval_card"), "名字不合法就不该弹卡"
    assert not h.created
    assert not h.of("error"), "这不是错误，是把话说回给模型"

    tool_msg = [m for m in h.fake.requests[1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith("error:")


async def test_unknown_tool_is_protocol_isolated_and_final():
    """★ [v1.0.22.1] 未知工具调用在协议层被隔离（StreamAssembler 校验），
    与 AgentLoop 同语义：协议错误即终局——人话收尾，不重试、不弹卡、不建项目。"""
    h = Harness([
        tool_stream("propose_agents", {"proposed": []}),   # 知知没有这个工具
    ])
    await h.run()

    assert not h.of("approval_card")
    assert not h.created
    assert len(h.fake.requests) == 1, "★ 协议错误是终局，不给模型第二轮"
    msg = h.of("message")[-1]
    assert msg["agent_id"] == ZINNIA
    assert "没处理明白" in msg["content"], "人话收尾（本地兜底）"


# ═══════════════════════════════════════════════════════════════
# 三、异常不倒引擎
# ═══════════════════════════════════════════════════════════════

async def test_http_500_becomes_error_event():
    h = Harness([500])
    await h.run()                      # 不抛异常本身就是断言

    err = h.of("error")
    assert len(err) == 1
    assert err[0]["agent_id"] == ZINNIA
    assert "500" in err[0]["message"]


async def test_no_api_key_is_a_readiness_state_not_a_chat_error():
    """Readiness gate owns missing-model UX; the Agent must not emit a red chat error."""
    object.__setattr__(CONFIG, "deepseek_api_key", "")

    hub = Hub()
    created: list[str] = []

    async def create(name: str) -> tuple[str, str]:
        created.append(name)
        return "p_x", name

    agent = ZinniaAgent(create_project=create)       # 不注入 factory → 走真实分支
    events: list[dict[str, Any]] = []

    async def emit(p: dict[str, Any]) -> dict[str, Any]:
        ev = await hub.emit(PLATFORM_PROJECT_ID, p)
        events.append(ev)
        return ev

    await agent.run_turn(
        Turn(PLATFORM_PROJECT_ID, "知知", "你好", []), emit, Gate(hub, PLATFORM_PROJECT_ID),
    )

    assert not [e for e in events if e["type"] in {"error", "message"}]
    assert [e for e in events if e["type"] == "agent_idle"]
    assert not created


async def test_many_tool_rounds_continue_until_the_model_replies():
    rounds = 10
    h = Harness([
        *[
            tool_stream("create_project", {"project_name": f"项目{i}"}, call_id=f"call_{i}")
            for i in range(rounds)
        ],
        reply_stream("这些提议都处理完了。"),
    ])
    approver = asyncio.create_task(auto_approve(h.gate, "approved", n=rounds))
    await h.run()
    await approver

    assert len(h.of("approval_card")) == rounds
    assert len(h.of("approval_resolved")) == rounds
    assert not h.of("error")
    messages = h.of("message")
    assert messages[-1]["content"] == "这些提议都处理完了。"
    assert len(h.fake.requests) == rounds + 1


# ═══════════════════════════════════════════════════════════════
# 四、平台会话不是项目
# ═══════════════════════════════════════════════════════════════

async def test_platform_engine_starts_and_is_not_a_project(tmp_path):
    s = KnoweServer(data_dir=str(tmp_path))
    eng = s.start_platform()

    assert eng.project_id == PLATFORM_PROJECT_ID
    assert s.engines[PLATFORM_PROJECT_ID] is eng
    assert isinstance(eng.agent, ZinniaAgent)         # 平台引擎跑的是知知，不是总管
    assert s.store.load_projects() == []              # type: ignore[union-attr]

    await eng.stop()


async def test_server_side_create_project_persists_and_broadcasts(tmp_path):
    """知知调工具 → 项目真的建出来、真的落盘、真的广播 project_created。"""
    s = KnoweServer(data_dir=str(tmp_path))
    s.start_platform()

    pid, name = await s._zinnia_create_project("官网改版")

    assert s.hub.has(pid)
    assert pid in s.engines                                        # 引擎起来了
    rows = s.store.load_projects()                                 # type: ignore[union-attr]
    assert [(r["project_id"], r["name"]) for r in rows] == [(pid, "官网改版")]
    assert name == "官网改版"

    await s.engines[pid].stop()
    await s.platform.stop()                                        # type: ignore[union-attr]


def test_clean_name_rules():
    assert _clean_name("  官网  改版 ") == "官网 改版"
    with pytest.raises(ToolArgError):
        _clean_name("")
    with pytest.raises(ToolArgError):
        _clean_name(None)
    with pytest.raises(ToolArgError):
        _clean_name("名" * 41)


# ═══════════════════════════════════════════════════════════════
# 五、[v0.5] 建群卡走完全程 —— 项目真的落地，总管真的开口
# ═══════════════════════════════════════════════════════════════

async def test_card_approved_creates_the_project_with_the_cards_name(tmp_path):
    """★ 没人改名字 → 后端用卡上的原名兜底把项目建出来。"""
    s = KnoweServer(data_dir=str(tmp_path))
    s.start_platform()
    eng = s.platform
    assert eng is not None

    # 手动挂一张建群卡（等价于知知调了工具）
    task = asyncio.create_task(eng.gate.propose(
        tool="create_project", agent_id=ZINNIA,
        card_body={"project_name": "官网改版"}, timeout_s=3,
    ))
    await asyncio.sleep(0.02)
    card_id = eng.gate.pending_cards[0]["approval_id"]

    await s._maybe_create_from_card(eng, card_id)     # server 在 approve 那一刻做的事
    eng.resolve(card_id, "approved")
    assert await task == "approved"

    pid = s._project_id_for_approval(card_id)
    assert s.hub.has(pid)                                       # ★ 项目真的建出来了
    assert s.hub.projects[pid].name == "官网改版"
    assert [r["name"] for r in s.store.load_projects()] == ["官网改版"]   # type: ignore[union-attr]

    for e in list(s.engines.values()):
        await e.stop()


async def test_frontend_renamed_project_wins(tmp_path):
    """★ 用户在卡上改了名字 → 前端先发 create_project（同一个 id），后端不再重复建。"""
    s = KnoweServer(data_dir=str(tmp_path))
    s.start_platform()
    eng = s.platform
    assert eng is not None

    task = asyncio.create_task(eng.gate.propose(
        tool="create_project", agent_id=ZINNIA,
        card_body={"project_name": "知知提的名字"}, timeout_s=3,
    ))
    await asyncio.sleep(0.02)
    card_id = eng.gate.pending_cards[0]["approval_id"]
    pid = s._project_id_for_approval(card_id)

    # 前端用它改过的名字建（id 是从 card_id 确定性算出来的，两边算得一样）
    await s.create_project(pid, "用户改的名字")
    await s._maybe_create_from_card(eng, card_id)      # 后端兜底：发现已存在 → 什么都不做
    eng.resolve(card_id, "approved")
    await task

    assert s.hub.projects[pid].name == "用户改的名字"   # ★ 用户说了算
    assert len(s.store.load_projects()) == 1           # type: ignore[union-attr]
    #                                                     ★ 只有一个项目，没建重

    for e in list(s.engines.values()):
        await e.stop()


async def test_coordinator_speaks_first_in_a_new_project(tmp_path):
    """★ [#10] 新群不能是个死群——总管要主动说第一句话。"""
    object.__setattr__(CONFIG, "kickoff", True)     # Fake 档默认关，测试里显式打开
    # FakeAgent has no persisted model binding, so this Coordinator-only behavior test
    # deliberately disables the unrelated readiness barrier.
    with patch.dict(os.environ, {"MODEL_READINESS_GATE_V1": "0"}):
        s = KnoweServer(data_dir=str(tmp_path))
        pid = s._allocate_canonical_project_id()
        await s.create_project(pid, "官网改版")

        eng = s.engines[pid]
        for _ in range(200):                      # 等引擎把 kickoff 那个回合跑起来
            if s.hub.projects[pid].seq > 0:
                break
            await asyncio.sleep(0.01)

        events = s.hub.projects[pid].ring.events()
        assert events, "★ 新群里必须有人先开口，不能一片死寂"
        assert events[0]["agent_id"] == "coordinator"

        # kickoff **不是用户消息**——不该出现 user_echo，不然界面上会凭空多出一条用户发言
        assert not [e for e in events if e["type"] == "user_echo"]

        await eng.stop()
    object.__setattr__(CONFIG, "kickoff", False)


async def test_fake_mode_does_not_kickoff_by_default(tmp_path):
    """
    ★ Fake 档默认不 kickoff —— 因为 FakeAgent 的剧本会把那段系统指令**原样念给用户听**。
      （fake.py 在禁改清单里，改不了它的剧本，所以只能不喂给它。真 LLM 档正常。）
    """
    assert CONFIG.agent == "fake"
    assert CONFIG.kickoff is False

    s = KnoweServer(data_dir=str(tmp_path))
    pid = s._allocate_canonical_project_id()
    await s.create_project(pid, "安静的项目")
    await asyncio.sleep(0.1)

    assert s.hub.projects[pid].seq == 0     # 一个字都没说
    await s.engines[pid].stop()


async def test_existing_project_does_not_kickoff_twice(tmp_path):
    """重发 create_project（比如重连）不该让总管重复自我介绍。"""
    object.__setattr__(CONFIG, "kickoff", True)
    s = KnoweServer(data_dir=str(tmp_path))
    pid = s._allocate_canonical_project_id()
    await s.create_project(pid, "官网")
    await asyncio.sleep(0.05)
    before = s.hub.projects[pid].seq

    await s.create_project(pid, "官网")      # 再来一次
    await asyncio.sleep(0.05)

    assert s.hub.projects[pid].seq == before
    await s.engines[pid].stop()
    object.__setattr__(CONFIG, "kickoff", False)


# ── [v1.0.22.1-对齐 A] 用户称呼注入（与总管/Worker 同源 runtime_settings.user_name）──


def test_zinnia_user_block_has_name(monkeypatch) -> None:
    from backend import runtime_settings
    monkeypatch.setattr(runtime_settings, "user_name", lambda default="": "kai")
    agent = ZinniaAgent()
    block = agent._user_block()
    assert "kai" in block


def test_zinnia_user_block_empty_when_unset(monkeypatch) -> None:
    from backend import runtime_settings
    monkeypatch.setattr(runtime_settings, "user_name", lambda default="": "")
    agent = ZinniaAgent()
    assert agent._user_block() == ""


def test_zinnia_user_block_follows_rename(monkeypatch) -> None:
    """改名后下一轮立即生效（每轮现取，不缓存）。"""
    from backend import runtime_settings
    current = {"value": "kai"}
    monkeypatch.setattr(
        runtime_settings, "user_name", lambda default="": current["value"] or default,
    )
    agent = ZinniaAgent()
    assert "kai" in agent._user_block()
    current["value"] = "abc"
    assert "abc" in agent._user_block()


def test_zinnia_context_block_includes_user_and_recent_platform_memory(monkeypatch) -> None:
    """A+B 集成：称呼块 + 平台记忆 brief 都进每轮上下文。"""
    from backend import runtime_settings
    monkeypatch.setattr(runtime_settings, "user_name", lambda default="": "kai")
    agent = ZinniaAgent(
        harness_brief=lambda: "test1（成员2）— 进行中",
        platform_brief=lambda: "Knowe 1.0",
        memory_brief=lambda: "[2026-08-03 06:30] 用户说「我是谁」→ 知知答「欢迎」",
    )
    ctx = agent._context_block()
    assert "kai" in ctx
    assert "我是谁" in ctx
    assert "test1" in ctx

