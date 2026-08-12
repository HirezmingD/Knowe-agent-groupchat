# knowe v0.22 — 测试：角色能力库（问题四）
"""
投诉：用户要「浏览小红书博主主页截图」「搜索下载照片」，
总管把活全派给了 **Shiloh · 技术写作**。

总管眼里的团队只有「Shiloh（技术写作）」七个字 —— 24 个角色标签，24 个盲盒。
他没有任何依据能判断「技术写作适不适合爬网页」，于是挑了名字里带「技术」的那个。

这里守两件事：
  ① 表和表对得上（前缀是和前端的契约，错一个就是花名册上一排「Agent」）
  ② 那两条投诉对应的判断依据，真的送到了**总管做决定的那一刻**
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import roles, tools_knowe                              # noqa: E402
from backend.roles import (                                         # noqa: E402
    ROLE_PROFILES, catalog_for_tool, identity_block, profile_for,
    profile_for_agent_id, roster_hint,
)


class FakeEngine:
    project_id = "p"
    workspace_root = Path("/tmp/ws")
    internal_workspace = Path("/tmp/int")


# ═══════════ 和前端的契约 ═══════════

def test_role_tables_match_exactly() -> None:
    """
    ★ KNOWN_ROLES 是**和前端共享的契约**（state.ts DEFAULT_ROLE_TYPES：前缀 → 角色）。

      少一个 → 那个角色又变回盲盒（问题四原样复发）；
      多一个 → 前端认不出这个前缀，花名册上冒出「Agent」
      （那正是 KNOWN_ROLES 当初要解决的问题）。
    """
    known, profiled = set(tools_knowe.KNOWN_ROLES), set(ROLE_PROFILES)
    assert known == profiled, f"两张表对不上：只在 KNOWN_ROLES {known-profiled}；只在 ROLE_PROFILES {profiled-known}"


def test_labels_are_identical() -> None:
    for prefix, prof in ROLE_PROFILES.items():
        assert prof.label == tools_knowe.KNOWN_ROLES[prefix]
        assert prof.prefix == prefix


def test_no_new_prefixes_were_invented() -> None:
    """
    ★ 「不改前端」是硬约束。从 agency-agents 引进 100+ 个角色很诱人，
      但每个新前缀都要改 state.ts。所以这一版是**不加角色，加认识**。
    """
    assert len(ROLE_PROFILES) == 24


def test_every_role_says_what_it_is_not_for() -> None:
    """
    ★ 只写「擅长」是不够的，而且这一点反直觉：

        技术写作：擅长 写文档、教程、API 参考

      总管要派「爬网页」时会**自己圆过来**：「爬网页也是搜集素材，素材是写文档的一部分」。
      LLM 极其擅长把任何任务论证成任何角色的分内事。**排除比包含更有判别力。**
    """
    for prefix, prof in ROLE_PROFILES.items():
        assert prof.good_at.strip(), f"{prefix} 没写擅长"
        assert prof.not_for.strip(), f"{prefix} 没写「不适合」——这一栏才是划边界的那一栏"


# ═══════════ 直接对着那条投诉 ═══════════

def test_technical_writer_is_explicitly_not_a_scraper() -> None:
    """★ 这一条就是 v0.22 问题四的原样复现。"""
    prof = ROLE_PROFILES["writer"]
    assert "爬虫" in prof.not_for and "网页抓取" in prof.not_for
    assert "文档" in prof.good_at


def test_scraping_work_has_an_obvious_home() -> None:
    """
    ★ 需要的角色**本来就在**这 24 个里 —— 缺的从来不是角色，是判断依据。
      （soul 里甚至早就写着「做爬虫的归后端」，只是埋在第 154 行。）
    """
    assert "爬虫" in ROLE_PROFILES["be"].good_at
    assert "数据采集" in ROLE_PROFILES["da"].good_at


def test_lookup_by_label_and_prefix_and_id() -> None:
    assert profile_for("writer").label == "技术写作"
    assert profile_for("技术写作").prefix == "writer"
    assert profile_for_agent_id("writer_1").prefix == "writer"
    assert profile_for_agent_id("be_2").prefix == "be"
    assert profile_for("不存在的角色") is None
    assert profile_for_agent_id("zzz_1") is None
    assert profile_for("") is None


# ═══════════ 送到决策点 ═══════════

def test_roster_hint_is_short() -> None:
    """★ 它每一轮都在总管眼前，还要乘以 8 个人。长了就是在给问题二添柴。"""
    for role in tools_knowe.KNOWN_ROLES.values():
        hint = roster_hint(role)
        assert hint and len(hint) < 45, f"{role} 的花名册提示太长了：{hint}"


def test_roster_hint_carries_the_boundary() -> None:
    hint = roster_hint("技术写作")
    assert "擅长" in hint and "不适合" in hint
    assert "爬虫" in hint                       # 派活那一刻，边界就在他眼前


def test_catalog_lands_in_the_hire_tool() -> None:
    """
    ★ 目录放在 propose_agents 的**工具描述**里，不在人设里：
      模型是在「我要加人」的那一刻读 schema 的，那正是「挑哪个角色」的决策点。
      顺带把人设里那份光秃秃的清单删掉 —— 问题四拿到更多信息，问题二少扛一段。
    """
    reg = tools_knowe.build_coordinator_registry(FakeEngine())
    desc = reg.get("propose_agents").description
    for role in tools_knowe.KNOWN_ROLES.values():
        assert role in desc, f"目录里漏了 {role}"
    assert "爬虫" in desc
    assert "这活该谁来想" in desc               # 挑脑子，不是挑工具箱


def test_assign_tool_tells_him_to_match_the_brain() -> None:
    reg = tools_knowe.build_coordinator_registry(FakeEngine())
    desc = reg.get("propose_next").description
    assert "擅长/不适合" in desc
    assert "技术写作去爬网页" in desc           # 把那次事故直接写进工具描述
    assert "别只看谁闲着" in desc


def test_role_list_left_the_system_prompt() -> None:
    """
    ★ 问题二说总管的提示词在膨胀。问题四要更多信息。
      两个需求方向相反 —— 解法是**换个地方放**，而不是硬塞。
    """
    soul = (Path(roles.__file__).parent / "souls" / "coordinator.txt").read_text("utf-8")
    assert "前端(fe) · 后端(be)" not in soul, "那份光秃秃的角色清单还在人设里"
    assert "propose_agents` 的工具说明里有一张**完整的角色目录**" in soul


# ═══════════ 成员自己的「我是谁」 ═══════════

def test_identity_gives_the_worker_a_way_of_thinking() -> None:
    block = identity_block("writer_1", "技术写作")
    assert "【你的专业】技术写作" in block
    assert "看家本领" in block


def test_identity_never_reads_as_a_permission_denial() -> None:
    """
    ★ 最容易写坏的一条：「不适合」说的是**该谁来做更好**，不是**你被禁止**。

      写成禁令的话，一个技术写作接到爬虫任务会当场撂挑子 ——
      而用户要的是活干完，不是一场关于岗位职责的辩论。
      （何况他的工具箱和别人**完全一样**，他真做得到。）
    """
    block = identity_block("writer_1", "技术写作")
    assert "不是权限" in block
    assert "工具箱和队里其他人**完全一样**" in block
    assert "先做完" in block
    assert "绝不要因此罢工或推诿" in block


def test_identity_is_empty_for_unknown_roles() -> None:
    """认不出的角色 → 一个字都别编。"""
    assert identity_block("zzz_1", "外星人") == ""


def test_identity_block_is_wired_into_the_worker_prompt() -> None:
    from backend import engine
    src = Path(engine.__file__).read_text("utf-8")
    body = src.split("def _identity_block")[1][:1600]
    assert "roles.identity_block" in body
