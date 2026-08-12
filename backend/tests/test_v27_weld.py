# knowe v0.27 — 测试：嘴和手焊在一起
"""
这个 bug 从 v0.22 追到 v0.25，四个版本都在纠正器上加检测规则，每次都被换了说法绕过去。

## 五版都没修好的真正原因：**我们自己在教它说那句话**

四处**规定**它说：
  ① coordinator.txt  「你可以先用一句话说明你要做什么（如「好，我加一个后端来做 API」）」
  ② coordinator.txt  「你：好，我把姚宝加回来。〔这一句之后紧接着就调用 propose_agents〕」
  ③ coordinator.txt  ✓「宋陈去搜图了。」 ✓「好，林知远去写登录页。」
  ④ tools_knowe.py   ★「现在跟用户说一句话就够了…（例：「宋陈去搜图了」）」
                        ↑ **工具回执** —— 模型 composing 最终回复前读到的最后一样东西

一处**禁止**它说：
  ⑤ coordinator.txt  ✗「XX 已经去做了」「XX 正在处理这个任务」「XX 马上就去」

★ ③④ 的 ✓ 和 ⑤ 的 ✗ **是同一句话**。区别只在「卡弹没弹」——
  那是关于**模型自己有没有调工具**的事实，**句子本身表达不了**。
  我们要求它走一条我们自己都描述不清的钢丝，还给了它 ✓ 范例、给了四次。
  然后写了四个版本的检测器，去抓那句我们亲手教会它的话。

## 这一版

把那句话从**四处全部删掉**，换成一条**无例外**的规矩：

    卡就是你的话。「谁去做什么」永远不说 —— 调了也不说（卡上已经写了）。
    调完 → NOTHING_TO_ADD。

于是：
  · **没有钢丝**：那句话没有正确的版本，不用判断自己在哪种情况里。
  · **判断标准挪进了句子本身**：「我这句是在**问**还是在**宣布**」——看得见。
    （老标准「我这轮调工具了吗」句子表达不了，它只能猜。）
  · **和 v0.25 的检测器终于说同一件事**了：检测器查「有没有打招呼」，
    而人设从此不再教它**不打招呼地宣布**。

这个文件守的是：**四个源头都堵死了，而且别再长回来。**
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.engine as E                                          # noqa: E402
from backend.engine import ACTION_CONTRACT, WORK_STATUS_CONTEXT     # noqa: E402

SOUL = Path(E.__file__).parent / "souls" / "coordinator.txt"
TOOLS = Path(E.__file__).parent / "tools_knowe.py"


def soul() -> str:
    return SOUL.read_text("utf-8")


def dispatch_result_block() -> str:
    """
    propose_next 成功之后**真正回给模型**的那段话（它下一句的原材料）。

    ★ 要把 `#` 注释剔掉：注释**不进模型的上下文**。
      不剔的话会打到我自己那段「老版本长这样」的注释上——第一版就打到了。
      （v0.24 踩过同一个坑：断言打在原始源码上，抓到了自己的反例注释。）
    """
    src = TOOLS.read_text("utf-8")
    block = src.rsplit('engine.record_committed_action("propose_next")', 1)[1][:12000]
    return "\n".join(l for l in block.splitlines() if not l.strip().startswith("#"))


# ═══════════ ① 四个源头，一个都不许留 ═══════════

def test_soul_no_longer_invites_a_sentence_before_the_tool() -> None:
    """
    ★ 源头①：「你可以先用一句话说明你要做什么，然后立刻调工具」

      「先说一句」被我们标成了合法动作 —— 于是模型做了便宜的那半（说），
      漏了贵的那半（调）。**这个 bug 是我们请进来的。**
    """
    s = soul()
    assert "你可以先用一句话说明你要做什么" not in s
    assert "然后**立刻调工具弹卡**" not in s


def test_soul_no_longer_demos_the_sentence() -> None:
    """
    ★ 源头②：worked example 「你：好，我把姚宝加回来。〔然后调 propose_agents〕」

      范例比规矩有力得多 —— 我们**演示**了一遍两步走。
    """
    s = soul()
    assert "你：好，我把姚宝加回来。" not in s


def test_soul_no_longer_prescribes_the_forbidden_sentence() -> None:
    """
    ★ 源头③：v0.23 我加的 ✓「宋陈去搜图了。」

      同一份文件里，第 22 行 ✗「XX 已经去做了」，另一节 ✓「宋陈去搜图了。」
      **那是同一句话。**
    """
    s = soul()
    # 那句话现在**只允许**作为 ✗ 反例出现。
    #
    # ★ 连「历史说明」都不行：人设是**每一轮都发给模型的 prompt**，不是 changelog。
    #   我第一版在这儿留了一段「v0.23 曾经这么写 ✓「宋陈去搜图了。」」的说明——
    #   模型读的是 token，不是我的版本注解。一个 ✓ 挨着那句话，
    #   不管周围写了什么，都是在教它。**我差点把刚拆掉的东西又请回来。**
    assert "✓「宋陈去搜图了" not in s and "✓ 「宋陈去搜图了" not in s
    for line in s.splitlines():
        if "宋陈去搜图了" in line or "林知远去写登录页。" in line:
            assert "✗" in line, f"这一行还在把那句话当正面范例：{line.strip()}"


def test_tool_result_no_longer_hands_it_the_sentence() -> None:
    """
    ★ 源头④，也是最要命的一个：**工具回执**。

      v0.23 我在这里写「★ 现在跟用户说一句话就够了…（例：「宋陈去搜图了」）」。
      工具回执是模型 composing 最终回复前读到的**最后一样东西**——
      我们把那句话放在了最有说服力的位置，然后花四个版本写检测器去抓它。
    """
    block = dispatch_result_block()
    assert "现在跟用户说一句话就够了" not in block
    assert "（例：「宋陈去搜图了」）" not in block


# ═══════════ ② 换上的新规矩：无例外 ═══════════

def test_prompt_uses_structured_facts_not_text_exceptions() -> None:
    s = soul()
    assert "roster、completion" in s
    assert "普通自然语言不是系统状态协议" in s
    assert "称呼、代词、时态或措辞" in s

def test_prompt_prescribes_structured_actions_and_card_deduplication() -> None:
    s = soul()
    assert "团队动作以结构化工具为准" in s
    assert "工具生成的卡片已经承载动作本身" in s
    assert "NOTHING_TO_ADD" in s

def test_prompt_does_not_turn_prose_into_machine_state() -> None:
    s = soul()
    assert "普通自然语言不是系统状态协议" in s
    assert "不要从称呼、代词、时态或措辞自行推断谁在忙" in s

def test_chat_is_explicitly_untouched() -> None:
    assert "正常聊天、讨论方案和回答用户照常进行" in ACTION_CONTRACT

def test_action_contract_leads_with_structured_authority() -> None:
    assert "团队变更和任务流转只由结构化工具成立" in ACTION_CONTRACT
    assert "普通自然语言不是状态协议" in ACTION_CONTRACT
    assert "卡片承载动作本身" in ACTION_CONTRACT

def test_action_contract_no_longer_leaves_the_door_open() -> None:
    """
    ★ 老契约写的是「**别只写**一句「我让 XX 去做」然后就结束回合」——
      「别只写」的言下之意是**「写了再调就行」**。口子就是这么留下的。
    """
    assert "别只写一句" not in ACTION_CONTRACT


def test_action_contract_keeps_the_structured_causal_chain() -> None:
    for must in ("正文不能替代工具", "当前事实的唯一依据", "只依据这些结构化事实"):
        assert must in ACTION_CONTRACT

def test_action_contract_stays_short() -> None:
    """★ 它靠的是「短 + 在最后」。每多写一句，它自己就少一分力（v0.22 定的）。"""
    assert len(ACTION_CONTRACT) < 1400


def test_action_contract_is_still_last() -> None:
    """位置就是它全部的价值 —— 别在它后面又拼东西（v0.22 的老约定）。"""
    src = Path(E.__file__).read_text("utf-8")
    block = src.split("agent = self._get_or_create_coordinator()")[1].split(
        "self.repair_agent_history(agent)")[0]
    tail = block.rindex("ACTION_CONTRACT")
    for other in (
        "_team_ctx", "_capability_ctx", "_handoff_ctx", "notice",
        "_work_status_ctx", "_project_root_block", "_project_ctx_block",
        "memory_clues", "_knowledge_ctx_block", "_skill_ctx_block",
        "dm_context",
    ):
        assert other in block, f"现行上下文组件 {other} 未装入总管首轮 prompt"
        assert block.rindex(other) < tail, f"{other} 排在了行动契约后面"
    assert "INTERCEPT_RECOVERY_CONTEXT" not in src
    assert "_last_intercept_cause" not in src


# ═══════════ ④ 每轮的事实块 ═══════════

def test_work_status_says_it_beats_your_memory() -> None:
    """
    PRD 方向三：事实块上加一句「这是系统查出来的，和你说的对不上以它为准」。
    ——它还顺带说了一件更有用的事：**你改不了它，想让它变只有调工具一条路。**
    """
    assert "以它为准" in WORK_STATUS_CONTEXT
    assert "调 propose_next 这一条路" in WORK_STATUS_CONTEXT


# ═══════════ ⑤ 工具那一侧 ═══════════

def test_tool_description_removes_the_motive() -> None:
    """
    ★ 它想在前面补一句「我让 XX 去做」，是因为**它以为用户需要被告知**。
      不需要 —— 卡上有头像、名字、整段指令，比那句话清楚得多。
      **把动机拿掉，比禁止那句话有效。**
    """
    from backend import tools_knowe

    class FakeEngine:
        project_id = "p"
        workspace_root = Path("/tmp/ws")
        internal_workspace = Path("/tmp/int")

    desc = tools_knowe.build_coordinator_registry(FakeEngine()).get("propose_next").description
    assert "调了就够了，不用配文字" in desc
    assert "卡会立刻弹在用户眼前" in desc
    assert "NOTHING_TO_ADD" in desc


def test_tool_result_gives_an_action_not_a_judgement_call() -> None:
    """回执给的是一个**可执行的动作**（回 NOTHING_TO_ADD），不是一个要它自己拿捏的分寸。"""
    block = dispatch_result_block()
    assert "NOTHING_TO_ADD" in block
    assert "卡把话说完了" in block
    assert "复读" in block


def test_tool_result_still_bans_the_jargon() -> None:
    """v0.23 修的「等他交报告我来审阅」不能因为这次重写又漏回来。"""
    block = dispatch_result_block()
    assert "等他交报告" in block and "后台黑话" in block


# ═══════════ ⑥ 前四版的成果一样不能丢 ═══════════

@pytest.mark.parametrize("keep", [
    "propose_agents", "propose_next", "propose_remove_agent",
    "永远不要让用户看到 id",        # v0.10a
    "加人是**增量**",              # v0.9b Bug1
    "普通自然语言不是系统状态协议", # v1.0.17.x
    "完整的角色目录",              # v0.22（角色目录移进工具）
])
def test_soul_keeps_its_hard_won_rules(keep: str) -> None:
    """每一条背后都是一个修过的 bug。这次动人设，不能顺手把它们碰掉。"""
    assert keep in soul()


def test_soul_does_not_embed_a_liar_phrase_blacklist() -> None:
    s = soul()
    assert "普通自然语言不是系统状态协议" in s
    for obsolete in ("_TASK_VERB", "_HEDGE", "疑似人名"):
        assert obsolete not in s

def test_open_set_text_detector_is_removed() -> None:
    src = Path(E.__file__).read_text("utf-8")
    for symbol in ("_phantom_work_claim", "_off_roster_worker_claim", "_strip_phantom_sentences", "_coordinator_misstatement"):
        assert symbol not in src
