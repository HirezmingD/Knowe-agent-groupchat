# knowe v0.26 — 测试：「我有新意见」原地 morph
"""
v0.24 / v0.25 两版都走「消息管道」，两次都失败：**总管把旧指令原样又发了一遍。**

v0.25 我把锅算在传输方式上，于是拼了一段更凶的 prompt。还是没用。
**所以那个归因是错的。**真正的原因是：

  ★ 我们让总管**在一个完整的 agent 回合里**做这件事。而那个回合里：
      · 有一条刚被作废搞炸的 tool_call，在冲它喊「原样重试」
      · 有几万字上下文，用户那句意见只是其中一行
      · 它有十几个工具可以调、无数种话可以说
    「按这条意见改一版指令」在那儿只是**众多选项之一**。
    它选错不是因为笨，是因为**有得选**。

这一版：`adjust_instruction` 不排回合、不进 inbox、不惊动 Harness ——
一次**定向的、一次性的**模型调用：这份指令 + 这条意见 → 改好的指令。
没有工具、没有历史、没有第二种可能的动作。
**它不会分心，因为没有东西可以让它分心。**

——传输从来不是问题，「让它在什么处境下做这件事」才是。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.engine as E                                          # noqa: E402
import backend.tools_knowe as TK                                    # noqa: E402
from backend import runtime_settings                                # noqa: E402
from backend.engine import ProjectEngine, _strip_instruction_fence  # noqa: E402
from backend.gate import Gate                                       # noqa: E402
from backend.hub import Hub                                         # noqa: E402
from backend.i18n_backend import msg                                 # noqa: E402

OLD = "用 React 做一个番外展示页，包含标题、正文和返回按钮。"
NEW = "用**纯 HTML**（不要 React）做一个番外展示页，包含标题、正文和返回按钮。"
FEEDBACK = "用纯HTML不要用React"


class FakeHub:
    def __init__(self) -> None:
        self.out: list[dict] = []
        self.projects: dict[str, object] = {}

    def get_or_create(self, pid):
        if pid not in self.projects:
            self.projects[pid] = type("P", (), {"pending_card": None, "name": pid})()
        return self.projects[pid]

    async def emit(self, pid, payload):
        self.out.append(dict(payload))
        return dict(payload)


def gate() -> Gate:
    return Gate(FakeHub(), "p")           # type: ignore[arg-type]


async def _pop_card(g: Gate, instruction: str = OLD, card_out=None):
    """起一张派活卡，返回 (task, card_id)。卡会一直挂着等人点。"""
    task = asyncio.ensure_future(g.propose(
        tool="propose_next", agent_id="coordinator",
        card_body={"target_id": "writer_1", "instruction": instruction},
        timeout_s=30, card_out=card_out,
    ))
    await asyncio.sleep(0)
    card_id = next(iter(g._pending))
    return task, card_id


# ═══════════ ① gate：改卡面，**不落定** ═══════════

def test_update_card_does_not_touch_the_future() -> None:
    """
    ★ 这是整个方案的地基：morph **碰都不碰 future**。
      卡还是 pending，倒计时接着走，approve/reject 的语义一个字没变，
      「恰好一次落定」那条硬保证完好无损。
    """
    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        pending = g.pending_of(card_id)
        assert pending is not None and not pending.future.done()

        card = await g.update_card(card_id, {"instruction": NEW})
        assert card is not None and card["instruction"] == NEW
        assert not pending.future.done(), "morph 把卡落定了 —— 这条线整个塌了"
        assert g.pending_of(card_id) is not None, "卡从 _pending 里掉出去了"

        g.resolve(card_id, "approved")
        assert await task == "approved"

    asyncio.run(go())


def test_update_card_reemits_the_same_card_id() -> None:
    """
    ★ 重发 approval_card（同一个 card_id），不新造事件类型：
      **卡的身份就是 card_id**，同一个 id 再来一次 = 「它变了」——幂等，天然对。
      契约一个字不用改；回放/快照白拿（后一条覆盖前一条）。
    """
    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        await g.update_card(card_id, {"instruction": NEW})

        cards = [x for x in g.hub.out if x["type"] == "approval_card"]   # type: ignore[attr-defined]
        assert len(cards) == 2
        assert cards[0]["card_id"] == cards[1]["card_id"] == card_id
        assert cards[0]["card"]["instruction"] == OLD
        assert cards[1]["card"]["instruction"] == NEW
        assert cards[1]["tool"] == "propose_next" and cards[1]["agent_id"] == "coordinator"
        # 没有多余的 approval_resolved —— 卡还在等
        assert not [x for x in g.hub.out if x["type"] == "approval_resolved"]  # type: ignore[attr-defined]

        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())


def test_update_card_on_a_settled_card_is_a_noop() -> None:
    """用户手快：调整还在路上，他先点了确认 → 这时候改卡面必须无声失败，不能炸。"""
    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        g.resolve(card_id, "approved")
        await task
        assert await g.update_card(card_id, {"instruction": NEW}) is None

    asyncio.run(go())


def test_update_card_unknown_id() -> None:
    async def go():
        assert await gate().update_card("ap_nope", {"instruction": NEW}) is None
    asyncio.run(go())


# ═══════════ ② card_out：派下去的必须是**卡上那份** ═══════════

def test_card_out_carries_the_morphed_instruction() -> None:
    """
    ★ 这条守的是一个**极安静**的 bug：

      卡面改好了、用户点了确认，可 handle_propose_next 手里攥的还是它自己那份
      局部变量 —— 于是卡上显示新指令、派下去的却是旧的。
      用户改了个寂寞，而且屏幕上一切正常，他永远查不出来。
    """
    async def go():
        g = gate()
        out: dict = {}
        task, card_id = await _pop_card(g, card_out=out)
        await g.update_card(card_id, {"instruction": NEW})
        g.resolve(card_id, "approved")
        await task
        assert out.get("instruction") == NEW, "回程没接上 → 派下去的还是旧指令"

    asyncio.run(go())


def test_card_out_is_optional() -> None:
    """组队卡 / 移除卡不传 card_out —— 一个字都不该受影响。"""
    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        g.resolve(card_id, "approved")
        assert await task == "approved"
    asyncio.run(go())


def test_propose_next_wires_card_out() -> None:
    """写了但没接线 = 白写。"""
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    body = src.split("async def handle_propose_next", 1)[1].split("async def handle_propose_remove_agent", 1)[0]
    assert "card_out=final_card" in body
    # 拿回程必须在 commit 之前，否则落盘的还是旧指令
    assert body.index("final_card.get(\"instruction\")") < body.index("commit_handoff_step")


# ═══════════ ③ engine.adjust_instruction ═══════════

def bare_engine(g: Gate) -> ProjectEngine:
    e = ProjectEngine.__new__(ProjectEngine)
    e.project_id = "p"
    e.gate = g
    e._feedback_flights = {}
    e._feedback_gen = {}
    return e


def test_adjust_replaces_the_instruction(monkeypatch) -> None:
    """★ 一次定向调用 → 卡面就地换新。全程不排回合、不进 inbox。"""
    async def fake_chat(messages, **kw):
        assert OLD in messages[1]["content"] and FEEDBACK in messages[1]["content"]
        return NEW
    monkeypatch.setattr(E.aux_client, "chat", fake_chat)

    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        e = bare_engine(g)
        res = await e.adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is True
        assert g.pending_of(card_id).card["instruction"] == NEW      # type: ignore[union-attr]
        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())
    monkeypatch.undo()


def test_adjust_prompt_has_one_job_and_no_alternatives() -> None:
    """
    ★ 这一版能成的全部原因：**没有东西可以让它分心**。
      系统提示必须把出口封死成一个——只输出正文，不要解释、不要围栏、不要抬头。
    """
    p = ProjectEngine.ADJUST_SYSTEM
    assert "只输出改写后的指令正文本身" in p
    assert "不要解释" in p and "不要用代码块包起来" in p
    assert "原样保留" in p              # 没提到的别顺手重写
    assert "以用户为准" in p            # 和原指令冲突时听谁的


@pytest.mark.parametrize("reply,expect", [
    ("```\n新指令\n```", "新指令"),
    ("```markdown\n新指令\n```", "新指令"),
    ("修改后的指令：新指令", "新指令"),
    ("新指令：新指令", "新指令"),
    ("  新指令  ", "新指令"),
])
def test_fence_is_stripped(reply: str, expect: str) -> None:
    """
    系统提示里已经明说不要围栏了，模型照样时不时来一个。
    **规矩里说了不算数的部分，就在代码里兜住**（v0.22 起反复验证过的一条）。
    """
    assert _strip_instruction_fence(reply) == expect


def test_adjust_rejects_empty_feedback() -> None:
    async def go():
        assert (await bare_engine(gate()).adjust_instruction("ap_x", "  "))["ok"] is False
    asyncio.run(go())


def test_adjust_on_missing_card_is_a_message_not_a_crash() -> None:
    """用户手快，卡刚被批了 → 给他一句人话，别让卡永远转圈。"""
    async def go():
        res = await bare_engine(gate()).adjust_instruction("ap_gone", FEEDBACK)
        assert res["ok"] is False and "已经不在等待中" in res["reason"]
    asyncio.run(go())


def test_adjust_only_task_cards(monkeypatch) -> None:
    """
    ★ 后端这边 tool 是 "propose_next"（前端 state.ts 才归一成 'task'）。
      写成 "task" 的话每一张卡都会被拒 —— 而且拒得很安静。
    """
    async def go():
        g = gate()
        task = asyncio.ensure_future(g.propose(
            tool="propose_agents", agent_id="coordinator",
            card_body={"proposed": [{"id": "fe_1", "role": "前端"}]}, timeout_s=30))
        await asyncio.sleep(0)
        card_id = next(iter(g._pending))
        res = await bare_engine(g).adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is False and "只有派活卡" in res["reason"]
        g.resolve(card_id, "approved")
        await task
    asyncio.run(go())


def test_adjust_reports_an_unchanged_instruction(monkeypatch) -> None:
    """
    ★ 模型把原文原样吐回来了 → **说出来**。
      别让用户对着一张没变的卡发呆——那正是 v0.24/v0.25 里他经历过的事，
      只不过那时候没人告诉他发生了什么。
    """
    monkeypatch.setattr(E.aux_client, "chat", lambda m, **k: _aio(OLD))

    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        res = await bare_engine(g).adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is False and "没有变化" in res["reason"]
        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())
    monkeypatch.undo()


def test_adjust_llm_failure_is_a_message_not_a_crash(monkeypatch) -> None:
    """这是用户点出来的交互 —— 出什么岔子都得给他一句人话。"""
    async def boom(messages, **kw):
        raise RuntimeError("网络炸了")
    monkeypatch.setattr(E.aux_client, "chat", boom)

    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        res = await bare_engine(g).adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is False and "网络炸了" in res["reason"]
        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())
    monkeypatch.undo()


def _aio(v):
    async def _f():
        return v
    return _f()


# ═══════════ ④ server 接线 ═══════════

def test_server_has_the_command(monkeypatch) -> None:
    monkeypatch.setattr(runtime_settings, "language", lambda: "zh")
    src = Path(Path(E.__file__).parent / "server.py").read_text("utf-8") \
        if (Path(E.__file__).parent / "server.py").exists() else None
    if src is None:
        pytest.skip("server.py 不在这棵树里")
    assert "async def _cmd_feedback_instruction" in src
    assert "eng.adjust_instruction(approval_id, feedback)" in src
    # 失败要出声：静默 = 卡永远转圈，比报错难受得多
    assert 'msg("server.py.337"' in src
    assert "没能按你的意见调整指令" in msg("server.py.337", reason="x")


# ═══════════ ⑤ 别打破 approve / reject ═══════════

def test_approve_and_reject_still_work() -> None:
    """它俩是控制面的另外两条路，和 morph 并列 —— 一个字都不许受影响。"""
    async def go():
        for verdict in ("approved", "rejected"):
            g = gate()
            task, card_id = await _pop_card(g)
            assert g.resolve(card_id, verdict) is True                # type: ignore[arg-type]
            assert await task == verdict
            resolved = [x for x in g.hub.out if x["type"] == "approval_resolved"]  # type: ignore[attr-defined]
            assert len(resolved) == 1 and resolved[0]["resolution"] == verdict
    asyncio.run(go())


def test_settle_still_happens_exactly_once() -> None:
    """gate 的两条硬保证之一。morph 之后也必须还是恰好一次。"""
    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        await g.update_card(card_id, {"instruction": NEW})
        await g.update_card(card_id, {"instruction": NEW + "再改一次"})
        g.resolve(card_id, "approved")
        await task
        g.resolve(card_id, "rejected")           # 迟到的第二次 → 必须无效
        assert len([x for x in g.hub.out if x["type"] == "approval_resolved"]) == 1  # type: ignore[attr-defined]
    asyncio.run(go())


def test_cancel_all_still_cancels() -> None:
    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        assert g.cancel_all("cancelled") == 1
        with pytest.raises(Exception):
            await task
    asyncio.run(go())


def test_morph_can_repeat() -> None:
    """用户可以一直提意见，直到满意为止。"""
    async def go():
        g = gate()
        out: dict = {}
        task, card_id = await _pop_card(g, card_out=out)
        for i in range(3):
            await g.update_card(card_id, {"instruction": f"第 {i} 版"})
            assert g.pending_of(card_id) is not None      # 每次都还在等
        g.resolve(card_id, "approved")
        await task
        assert out["instruction"] == "第 2 版"             # 派下去的是最后那一版
    asyncio.run(go())


# ═══════════ ⑥ [v1.0.24.3] 意见留痕 + 改卡回执 ═══════════

def test_adjust_appends_feedback_history(monkeypatch) -> None:
    """改一次 → 意见原文进卡体 feedback_history（card_out 回程 + 前端 badge 的原料）。"""
    async def fake_chat(messages, **kw):
        return NEW
    monkeypatch.setattr(E.aux_client, "chat", fake_chat)

    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        e = bare_engine(g)
        res = await e.adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is True
        card = g.pending_of(card_id).card                # type: ignore[union-attr]
        assert card["instruction"] == NEW
        assert card["feedback_history"] == [FEEDBACK]
        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())
    monkeypatch.undo()


def test_adjust_appends_multiple_rounds(monkeypatch) -> None:
    """改两轮 → 意见按轮累积（每轮原文都留、顺序不乱）。"""
    calls: dict[str, int] = {"n": 0}

    async def fake_chat(messages, **kw):
        calls["n"] += 1
        return NEW if calls["n"] == 1 else "再改一版：" + NEW
    monkeypatch.setattr(E.aux_client, "chat", fake_chat)

    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        e = bare_engine(g)
        assert (await e.adjust_instruction(card_id, "第一轮意见"))["ok"] is True
        assert (await e.adjust_instruction(card_id, "第二轮意见"))["ok"] is True
        card = g.pending_of(card_id).card                # type: ignore[union-attr]
        assert card["feedback_history"] == ["第一轮意见", "第二轮意见"]
        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())
    monkeypatch.undo()


def test_adjust_failure_does_not_append(monkeypatch) -> None:
    """LLM 原样吐回（失败）→ 卡面不动，feedback_history 不出现（零污染）。"""
    monkeypatch.setattr(E.aux_client, "chat", lambda m, **k: _aio(OLD))

    async def go():
        g = gate()
        task, card_id = await _pop_card(g)
        e = bare_engine(g)
        res = await e.adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is False
        card = g.pending_of(card_id).card                # type: ignore[union-attr]
        assert card["instruction"] == OLD
        assert "feedback_history" not in card
        g.resolve(card_id, "approved")
        await task

    asyncio.run(go())
    monkeypatch.undo()


class _FakeEngine:
    """handle_propose_next 的轻量替身：只实现 handler 走过的接口，其余照常。"""

    def __init__(self) -> None:
        self.project_id = "p"
        self.gate = gate()
        self.calls: list[tuple] = []

    def dispatch_frozen(self) -> bool:
        return False

    def roster(self) -> dict[str, str]:
        return {"writer_1": "写手"}

    def member_name(self, aid: str) -> str:
        return {"writer_1": "王五"}.get(aid, aid)

    def worker_is_busy(self, _tid: str) -> bool:
        return False

    def commit_handoff_step(self, **kw) -> dict[str, str]:
        self.calls.append(("commit", kw))
        return {"step": "01", "instruction_path": "/x/handoffs/01.md"}

    def record_committed_action(self, *a) -> None:
        self.calls.append(("rca", a))

    def record_dispatch(self, *a) -> None:
        self.calls.append(("rd", a))

    def start_committed_workers(self) -> None:
        pass

    async def emit(self, payload) -> None:
        self.calls.append(("emit", payload))

    async def on_proposal_rejected(self, *a) -> None:
        pass


async def _propose_next_receipt(engine) -> tuple[Any, str]:
    """起一张派活卡 → handler 挂起等审批 → 返回 (回执 future, card_id)。"""
    handler = TK.build_coordinator_registry(engine).get("propose_next").handler
    fut = asyncio.ensure_future(handler(
        {"target_id": "writer_1", "instruction": OLD},
        agent_id="coordinator",
    ))
    await asyncio.sleep(0)
    card_id = next(iter(engine.gate._pending))
    return fut, card_id


def test_propose_next_receipt_carries_amendment(monkeypatch) -> None:
    """改卡后确认 → 回执追加「已发送 + N 轮意见 + 最终指令」：PM 的收尾原料不再对着旧指令。"""
    async def fake_chat(messages, **kw):
        return NEW
    monkeypatch.setattr(E.aux_client, "chat", fake_chat)

    async def go():
        engine = _FakeEngine()
        fut, card_id = await _propose_next_receipt(engine)

        res = await bare_engine(engine.gate).adjust_instruction(card_id, FEEDBACK)
        assert res["ok"] is True

        engine.gate.resolve(card_id, "approved")
        out = json.loads(await fut)
        assert out["status"] == "ok"
        m = out["message"]
        assert "任务卡片已成功发送给 王五" in m        # 明示派活成功，PM 不再问「要不要派活」
        assert "以卡面最终指令为准" in m
        assert "【用户意见（共 1 轮）】" in m
        assert FEEDBACK in m                          # 意见原文逐字在场
        assert "【最终指令】" in m
        assert NEW in m                               # 新指令全文在场

    asyncio.run(go())
    monkeypatch.undo()


def test_propose_next_receipt_unchanged_when_not_amended() -> None:
    """没改过卡的派活 → 回执与以前逐字一致（零回归锚点）。"""
    async def go():
        engine = _FakeEngine()
        fut, card_id = await _propose_next_receipt(engine)
        engine.gate.resolve(card_id, "approved")
        out = json.loads(await fut)
        assert out["status"] == "ok"
        m = out["message"]
        assert "以卡面最终指令为准" not in m
        assert "【最终指令】" not in m

    asyncio.run(go())


# ═══════════ ⑥ 回归：审批通过 → 任务信封真实落盘 ═══════════

def test_approved_handoff_persists_envelope_with_authorization_ref() -> None:
    """
    ★ 2026-08-06 线上回归（循环派卡根因）：

      workflow 整删时误删了 _create_task_envelope 的 authorization_ref /
      approval_origin 两个参数（签名 + origin 块），但 propose_next 审批通过
      的调用点仍在传 → approve 当场 TypeError「got an unexpected keyword
      argument」→ 卡显示已确认但任务从未落盘 → PM 收到工具报错 → 重试 → 循环。

      这条走的是**真实 commit_handoff_step → TaskEnvelope 落盘**链路，
      堵住「确认→派活」的测试盲区（此前所有测试都在 gate / mock 层）。
    """
    root = Path(tempfile.mkdtemp())
    engine = ProjectEngine(Hub(), "p1", agent=None, workspace_root=root)
    engine._roster = {"writer_1": "技术写作"}
    engine._names["writer_1"] = "Shiloh"

    result = engine.commit_handoff_step(
        target_id="writer_1",
        instruction="写一份季度总结报告",
        decision="approved",
        keyword="季度总结",
    )
    assert str(result["step"])

    envelopes_dir = engine.internal_workspace / "runtime" / "task-envelopes"
    envelope_files = list(envelopes_dir.rglob("*.json"))
    assert envelope_files, "审批通过后信封没有落盘 —— 派活是假的，PM 必然重试"
    envelope = json.loads(envelope_files[0].read_text(encoding="utf-8"))
    origin = envelope["metadata"]["origin"]
    assert origin["authorization_ref"], "origin.authorization_ref 为空 —— 授权引用丢了"
    assert origin["approval_origin"] == "propose_next:user_approval"
