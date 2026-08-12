# knowe v0.24 — 测试：拒绝之后不许再弹同一张卡
"""
线上那一幕：

    用户点「拒绝」派活卡
    → **立刻又弹出一张一模一样的卡**
    → 用户再拒
    → 总管连发三条互相打架的话：
        「明白了，刚才卡没弹成功。我现在重新提议——让龙苗把第二篇番外做成网页…」
        「明白了，那先不派。你不想让龙苗做这个网页的话，你想怎么处理这篇番外？」
        「好的，那先不动。你想好了告诉我就行。」

## 根因是 v0.22 的纠正器（我写的）

第一句话是关键证据：「刚才**卡没弹成功**。我现在**重新提议**」——
那不是模型在瞎猜，是它在**逐字执行我们塞给它的纠正**：

    _coordinator_misstatement 的纠正词：
      「你**没有调用 propose_next**，所以**审批卡没有弹出来**……
        ① 这活确实该派 → **立刻调用 propose_next**」

拒绝之后的收口回合，纠正器的两个前提**都成立**：
  · 这一轮没调 propose_next？ 对 —— 因为用户说了不
  · 有人在干活吗？           没有 —— 因为用户说了不

★ 纠正器分不清这两件事：
    「他从没派过活」         → 撒谎，该纠正
    「他派了，用户按了拒绝」 → **用户的决定**，天经地义
  拒绝之后，「没人在干活」不是谎，是**用户要的结果**。前提整个反过来了。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.engine as E                                          # noqa: E402
from backend.engine import COORDINATOR, REJECTION_FOLLOWUP, ProjectEngine   # noqa: E402


def bare() -> ProjectEngine:
    e = ProjectEngine.__new__(ProjectEngine)
    e.project_id = "p"
    e._roster = {"writer_1": "技术写作", "be_1": "后端"}
    e._workers_with_open_activity = set()
    e._committed_actions_this_turn = set()
    e._answer_retry_used_this_turn = False
    e._rejection_pending = False
    e._rejection_followup_queued = False
    e._agents = {}
    e.member_name = lambda a: {"writer_1": "龙苗", "be_1": "Wolf"}.get(a, a)  # type: ignore
    e.inbox = asyncio.Queue()
    e.gate = type("G", (), {"cancel_all": lambda self, why: 0})()
    return e


# ═══════════ ① 拒绝立旗 ═══════════

def test_rejection_raises_the_flag_and_queues_one_followup() -> None:
    e = bare()
    asyncio.run(e.on_proposal_rejected("rejected", "派活"))
    assert e._rejection_pending is True
    assert e.inbox.qsize() == 1
    assert e.inbox.get_nowait()["content"] == REJECTION_FOLLOWUP


def test_only_one_followup_per_rejection() -> None:
    """
    ★ 「连发三条」的一半原因：followup 攒在队列里，一条一个回合，一个回合一句话。
      纠正器再弹一张卡、用户再拒一次 → 又一条 followup → 又一句话。
    """
    e = bare()
    for _ in range(3):
        asyncio.run(e.on_proposal_rejected("rejected", "派活"))
    assert e.inbox.qsize() == 1, "一次拒绝只该排一个收口回合"


@pytest.mark.parametrize("decision", ["timeout", "cancelled"])
def test_only_rejection_freezes(decision: str) -> None:
    """
    超时 = 用户可能只是走开了；取消 = 用户自己发了新消息。
    两种都不是「他说不」——不该冻，也不该塞 followup（老行为，别碰坏）。
    """
    e = bare()
    asyncio.run(e.on_proposal_rejected(decision, "派活"))
    assert e._rejection_pending is False
    assert e.inbox.qsize() == 0


# ═══════════ ② 收口回合不跑纠正器 ═══════════

def test_rejection_followup_has_no_semantic_answer_retry() -> None:
    """Rejection handling is structural; Runtime never retries based on prose meaning."""
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    assert "_is_unjustified_nothing_to_add" not in src
    assert "_answer_retry_used_this_turn" not in body
    assert "_coordinator_misstatement" not in body
    assert "_phantom_work_claim" not in body

def test_rejection_followup_has_no_free_text_reproposal_nudge() -> None:
    """The follow-up relies on the structured rejection flag, not a competing text judge."""
    src = Path(E.__file__).read_text("utf-8")
    assert "_coordinator_misstatement" not in src
    assert "_phantom_work_claim" not in src
    assert "审批卡没有弹出来" not in src
    assert "立刻调用 propose_next" not in src

def test_flag_is_cleared_after_the_followup_turn() -> None:
    """
    ★ 旗必须降。不降 → 纠正器被永久关掉 → v0.22 守的「总管嘴上说派了活其实没派」
      那道防线就哑了。那可是这个产品最不能丢的一条。
    """
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _process_turn(")[1][:12000]
    assert "content == REJECTION_FOLLOWUP" in body
    assert "self._rejection_pending = False" in body
    assert "finally:" in body.split("content == REJECTION_FOLLOWUP")[0], "降旗要放在 finally 里"


def test_user_speaking_thaws_immediately() -> None:
    """用户接着说「那让 Wolf 来做」→ 那是新指令，propose_next 必须立刻能用。"""
    e = bare()
    e._rejection_pending = True
    e._rejection_followup_queued = True
    asyncio.run(e.submit("那让 Wolf 来做"))
    assert e._rejection_pending is False
    assert e.dispatch_frozen() is False


# ═══════════ ③ 硬冻结（不是求它） ═══════════

def test_dispatch_frozen_reflects_the_flag() -> None:
    e = bare()
    assert e.dispatch_frozen() is False
    e._rejection_pending = True
    assert e.dispatch_frozen() is True


def test_propose_next_checks_the_freeze_in_code() -> None:
    """
    ★ REJECTION_FOLLOWUP 里**本来就写着**「不要重新提案」—— 线上照样弹了第二张。
      因为另一段 prompt（纠正器）正朝反方向喊，而模型听了后者。
      **两段 prompt 打架时谁也不知道哪段会赢，所以这一道必须是代码。**
    """
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    body = src.split("async def handle_propose_next")[1][:8000]
    assert "engine.dispatch_frozen()" in body
    # 冻结检查必须排在参数校验**之前** —— 不然它会先因为别的理由报错，
    # 模型看到「instruction 必须是非空字符串」只会重试，而不是收口。
    assert body.index("dispatch_frozen") < body.index("instruction 必须是非空字符串")


def test_frozen_reply_tells_it_what_to_do_instead() -> None:
    """
    回执用 _ok 不用 _err：它没做错什么，是此刻不该做。
    报错会让它以为要重试；这里直接给下一步。
    """
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    body = src.split("async def handle_propose_next")[1][:8000]
    # 切到**下一条语句的开头**为止。切到 "instruction 必须是非空字符串" 会把
    # 它前面那半句 `return _err(` 一起圈进来，然后误判成「冻结块里有 _err」。
    block = body[body.index("dispatch_frozen"):body.index("if not isinstance(instruction")]
    assert "_ok(" in block and "_err(" not in block
    assert "不要再提案" in block
    assert "好的，那先不派" in block          # 给个现成的句子，别让它自己发挥


# ═══════════ ④ 过期的卡前解说词不许再发 ═══════════

def test_stale_pre_card_text_is_dropped_on_rejection() -> None:
    """A rejected/interrupted scope clears only its own buffered pre-card text."""
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    rejection_guard = 'if result.get("is_interrupted") and self._rejection_pending:'
    block = body.split(rejection_guard, 1)[1][:600]
    assert "_clear_stream_buffer(COORDINATOR)" in block
    assert "stream_reset" in block
    assert "return" in block

def test_normal_interrupt_still_speaks() -> None:
    """
    只掐「被拒绝打断」这一种。普通中断只在模型正文为空且后继会接话时静默；
    已生成的半句话仍按原文出站。
    """
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    assert 'if result.get("is_interrupted") and final == "":' in body
    assert "successor_will_speak = internal or not self.inbox.empty()" in body
    assert 'if result.get("is_interrupted") and self._rejection_pending:' in body


# ═══════════ ⑤ 别把 v0.22 的防线拆了 ═══════════

def test_exact_control_marker_is_consumed_without_semantic_retry() -> None:
    """The exact framework marker may be stripped, but Runtime never judges or reruns it."""
    e = bare()
    assert e.absorb_markers("NOTHING_TO_ADD") == ""
    assert e.absorb_markers("我建议保留 NOTHING_TO_ADD 这个字样。") == "我建议保留 NOTHING_TO_ADD 这个字样。"
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    assert "_is_unjustified_nothing_to_add" not in src
    assert "_answer_retry_used_this_turn" not in body

def test_frozen_state_does_not_leak_into_the_normal_case() -> None:
    e = bare()
    assert e.dispatch_frozen() is False        # 默认解冻，别默认把提案关死
