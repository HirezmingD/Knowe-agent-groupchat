# knowe v0.28 — 测试：代码级焊死嘴和手
"""
v0.27 我找对了根因（我们自己在四处教它先说话），但把焊点放在了 prompt 上。
**prompt 是请求模型配合，模型可以不配合。**

而且我漏了第五处 —— 就在我正在改的那个文件里：

    engine.py REPORT_NOTICE：
      「要派下一件活，**先跟用户说清楚再 propose_next**；……
        **也不要在没跟用户交代之前就直接派活。**」

  ★ 第二句比我删掉的那四处都狠：那四处是**允许**先说，这一句是**要求**先说，
    否则不许调工具 —— 和 v0.27 的新规矩正面对撞。
  ★★ 而 REPORT_NOTICE **成员每交一次报告就注入一次**，而「审完报告 → 派下一件活」
     是整个产品里最常走的派活路径。最常见的那条路上，代码每次都在教它先说话。

  为什么我没找到：我普查的是「prompt 住在哪」——人设、工具描述、工具回执。
  **可引擎自己也在注入 prompt。** 我查了回执，没查通知。
  → 所以这个文件里有一条 `test_no_prompt_anywhere_teaches_speak_then_call`：
    它扫**所有模型读得到的字符串**。这类「人肉普查漏了一处」的事，不该再靠人肉。

## v0.28 的焊法：两半合起来，手不动嘴就出不了声

  · `propose_next` 有了 `note` → **想说话，就得穿过工具调用**
  · 回复正文里的派活复读 → **代码摘掉**（_strip_dispatch_echo）
  · 纠正之后还在说谎 → **代码摘掉，不许出门**（_strip_phantom_sentences）

  说和做不再是两条平行的路，而是同一个动作的两半。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.engine as E                                          # noqa: E402
from backend.engine import ProjectEngine   # noqa: E402

SOUL = Path(E.__file__).parent / "souls" / "coordinator.txt"


def bare() -> ProjectEngine:
    e = ProjectEngine.__new__(ProjectEngine)
    e.project_id = "p"
    e._roster = {"fe_1": "前端", "be_1": "后端", "writer_1": "技术写作"}   # id → **角色**
    e._workers_with_open_activity = {"fe_1"}
    e._dispatched_this_turn = []
    e._committed_actions_this_turn = set()
    e.member_name = lambda a: {                                   # type: ignore[assignment]
        "fe_1": "林知远", "be_1": "宋陈", "writer_1": "龙苗"}.get(a, a)
    return e


# ═══════════ ① 残留一：我 v0.27 漏掉的第五处 ═══════════
# [v1.0.37.2 R1] REPORT_NOTICE 已随取消 PM 审阅删除（两条通知链一起删），
# 原 test_report_notice_* 两个测试随之移除；结构性扫描测试保留。

def test_no_prompt_anywhere_teaches_speak_then_call() -> None:
    """
    ★★ 这条测试是 v0.27 那次漏掉的**结构性补救**。

      v0.27 我人肉普查了「prompt 住在哪」，漏了引擎注入的通知。
      人肉普查会漏 —— 那就别再人肉。这里扫**所有模型读得到的字符串**：
      人设全文 + 引擎/工具/闸门/服务端里每一个字符串常量。

      ★ 注释不算 —— 它们不进模型的上下文（v0.27 那条测试踩过这个坑）。
        ast.Constant 只会捞到真正的字符串字面量，注释天然不在里面。
    """
    banned = ("先跟用户说清楚", "说清楚再 propose", "交代之前就直接派",
              "先用一句话说明", "说一句话就够了", "先告诉用户再")
    offenders: list[str] = []

    for fname in ("engine.py", "tools_knowe.py", "gate.py"):
        f = Path(E.__file__).parent / fname
        if not f.exists():
            continue
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for b in banned:
                    if b in node.value:
                        offenders.append(f"{fname}:{node.lineno} 「{b}」")
    for i, line in enumerate(SOUL.read_text("utf-8").splitlines(), 1):
        for b in banned:
            if b in line:
                offenders.append(f"coordinator.txt:{i} 「{b}」")

    assert not offenders, (
        "又有地方在教「先说再调」了：\n  " + "\n  ".join(offenders)
        + "\n（v0.27 就是这么漏掉 REPORT_NOTICE 的 —— 人肉普查会漏。）"
    )


# ═══════════ ② note：想说话就得穿过工具调用 ═══════════

class FakeEngine:
    project_id = "p"
    workspace_root = Path("/tmp/ws")
    internal_workspace = Path("/tmp/int")


def test_propose_next_has_a_note_param() -> None:
    """
    ★ 这是「把嘴焊在手上」的**字面**实现。

      以前「想说一句关于这次派活的话」有两条路：写进正文（便宜、不用工具语法）
      或者调工具（贵）。模型永远走便宜那条 —— 于是说了却没调。
      现在只剩一条：**要说，就得穿过这个工具调用。**
    """
    from backend import tools_knowe
    params = tools_knowe.PROPOSE_NEXT_PARAMS["properties"]
    assert "note" in params
    d = params["note"]["description"]
    assert "显示在审批卡上" in d
    assert "只写**卡上没有的**东西" in d
    assert "会被系统摘掉" not in d              # Runtime 不承诺改写任何普通正文
    assert "别填" in d                          # 大多数时候不该填


def test_note_rides_along_on_the_card() -> None:
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    body = src.split("async def handle_propose_next", 1)[1].split("async def handle_propose_remove_agent", 1)[0]
    assert '"note": note' in body
    assert "契约一个字不用改" in body           # card 本来就是 dict


def test_note_is_cleaned() -> None:
    """卡是让人扫一眼就点的，不是读小作文的地方。"""
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    body = src.split("async def handle_propose_next", 1)[1].split("async def handle_propose_remove_agent", 1)[0]
    assert "len(note) > 300" in body


def test_approval_event_preserves_the_note_for_the_frontend() -> None:
    """交付包不含前端源码时，直接验证线上协议事件仍把 note 原样交给 UI。"""
    import asyncio
    from backend.gate import Gate
    from backend.hub import Hub

    async def go() -> None:
        hub = Hub()
        gate = Gate(hub, "p")
        pending = asyncio.create_task(gate.propose(
            tool="propose_next",
            agent_id="coordinator",
            card_body={"target_id": "fe_1", "instruction": "写首页", "note": "只先做首页"},
            timeout_s=2,
        ))
        await asyncio.sleep(0)
        events = hub.projects["p"].ring.events()
        card = next(event for event in events if event["type"] == "approval_card")
        assert card["card"]["note"] == "只先做首页"
        assert gate.resolve(card["card_id"], "rejected")
        assert await pending == "rejected"

    asyncio.run(go())


# ═══════════ ③ 已提交派活也不改写普通正文 ═══════════

@pytest.mark.parametrize("said", [
    "好，林知远去写登录页。",
    "已安排林知远处理，等他交报告我来审阅。",
    "好的，我让林知远马上开始。",
    "林知远正在写登录页了。",
    "登录页交给林知远。",
])
def test_dispatch_related_prose_is_preserved(said: str) -> None:
    """Structured dispatch state never turns ordinary sentences into a deletion protocol."""
    assert E._strip_control_markers(said) == said


def test_mixed_dispatch_and_substance_is_preserved_whole() -> None:
    said = "林知远去写登录页。我先只派这一件，注册页想等你确认需求。"
    assert E._strip_control_markers(said) == said


@pytest.mark.parametrize("said", [
    "我先只派了登录页那一件——注册页的需求我还没弄明白，想先问你。",
    "这件事我挑了林知远，因为要动前端；你要是想让宋陈来也行？",
    "登录页的接口文档在 docs/api.md，他会用到。",
    "我建议让林知远做这个，你看行吗？",
])
def test_substance_and_chat_survive(said: str) -> None:
    assert E._strip_control_markers(said) == said


def test_dispatch_echo_stripper_is_physically_removed() -> None:
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    assert "_strip_dispatch_echo" not in src
    assert "if final and self._dispatched_this_turn:" not in body
    assert not hasattr(ProjectEngine, "_strip_dispatch_echo")


def test_dispatched_is_cleared_每turn() -> None:
    """Dispatch tracking remains turn-local even though it no longer filters prose."""
    src = Path(E.__file__).read_text("utf-8")
    assert "self._dispatched_this_turn.clear()" in src


def test_record_dispatch_is_wired() -> None:
    from backend import tools_knowe
    src = Path(tools_knowe.__file__).read_text("utf-8")
    assert "engine.record_dispatch(target_id)" in src


# ═══════════ ④ 纠正器 → 阻断器 ═══════════

def test_outbound_path_has_no_open_set_prose_judge() -> None:
    src = Path(E.__file__).read_text("utf-8")
    body = src.split("async def _run_agent_turn(", 1)[1].split("\n    async def ", 1)[0]
    for symbol in ("_coordinator_misstatement", "_strip_phantom_sentences", "_phantom_work_claim"):
        assert symbol not in body

def test_ordinary_prose_has_no_phantom_stripper() -> None:
    e = bare()
    assert not hasattr(e, "_strip_phantom_sentences")

def test_nothing_to_add_is_only_a_card_presentation_contract() -> None:
    s = SOUL.read_text("utf-8")
    assert "没有卡片之外的新判断、风险或问题" in s
    assert "普通自然语言不是系统状态协议" in s

def test_prompt_structured_fact_contract_survives() -> None:
    s = SOUL.read_text("utf-8")
    assert "roster、completion" in s
    assert "普通自然语言不是系统状态协议" in s
    assert "工具生成的卡片已经承载动作本身" in s

def test_prompt_and_action_contract_share_card_deduplication() -> None:
    s = SOUL.read_text("utf-8")
    assert "正文不要机械复述卡片" in s
    assert "卡片承载动作本身" in ACTION_CONTRACT
    assert "NOTHING_TO_ADD" in ACTION_CONTRACT

def test_action_contract_stays_under_the_v022_budget() -> None:
    """★ v0.22 的规矩：它靠「短 + 在最后」。加了 note 也不许把它撑破。"""
    assert len(ACTION_CONTRACT) < 1000, f"契约 {len(ACTION_CONTRACT)} 字，超了 v0.22 的预算"


# ═══════════ ⑥ 别碰别的 ═══════════

def test_obsolete_text_detector_is_physically_absent() -> None:
    src = Path(E.__file__).read_text("utf-8")
    for symbol in ("_phantom_work_claim", "_off_roster_worker_claim", "_strip_phantom_sentences", "_coordinator_misstatement"):
        assert symbol not in src

def test_approve_reject_and_morph_untouched() -> None:
    """gate 的三条路（approve / reject / v0.26 的原地 morph）一个字都不许受影响。"""
    from backend import gate
    src = Path(gate.__file__).read_text("utf-8")
    assert "def resolve" in src and "def update_card" in src
    assert "card_out" in src
