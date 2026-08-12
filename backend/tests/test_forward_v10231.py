"""[v1.0.23.1] 转发功能后端测试：LLM 模板构造 + @ 解析防误路由。

覆盖 PRD R3 / 架构 B5、B7：
* build_forward_template 三路径（群聊有群名 / 无群名 / 空附言）+ 原文 @ 清洗；
* _active_user_text 新转发格式适配（配言段解析）+ 旧引用格式回归不破；
* resolve_mentions 集成：模板串/原文里的 @ 不触发路由，配言里的 @ 正常触发。
"""

from __future__ import annotations

from backend.mentions import MentionMember, _active_user_text, resolve_mentions
from backend.server import _FORWARD_LLM_MARKER, build_forward_template


# ═══════════════════════════════════════════════════════════════
# build_forward_template：三路径 + 原文清洗
# ═══════════════════════════════════════════════════════════════

def test_template_with_project_name() -> None:
    """群聊来源：带群名，模板逐字段断言。"""
    tpl = build_forward_template(
        username="kai", project_name="电商项目", source_name="陆可 · 前端",
        original="你好，需要帮忙吗？", comment="帮我看看这个",
    )
    assert tpl == "用户kai将电商项目中陆可 · 前端的消息你好，需要帮忙吗？转发了过来，并配言帮我看看这个"


def test_template_without_project_name() -> None:
    """知知/私聊来源：无群名时省略「{群名}中」。"""
    tpl = build_forward_template(
        username="kai", project_name=None, source_name="知知",
        original="早安", comment="",
    )
    assert tpl == "用户kai将知知的消息早安转发了过来，并配言"


def test_template_empty_comment() -> None:
    """空附言：模板结构保留，并配言后为空串（交 LLM 判断）。"""
    tpl = build_forward_template(
        username="用户", project_name="群A", source_name="陆可",
        original="原文", comment="",
    )
    assert tpl.endswith(_FORWARD_LLM_MARKER)
    assert tpl == "用户用户将群A中陆可的消息原文转发了过来，并配言"


def test_template_original_at_escaped() -> None:
    """原文里的 @ 转全角（架构 R1：防 rewrap_group_mention 误剥，原文不残缺）。"""
    tpl = build_forward_template(
        username="kai", project_name="群A", source_name="陆可",
        original="@小林 你把表格发我", comment="转发一下",
    )
    assert "＠小林" in tpl
    assert "@小林" not in tpl


# ═══════════════════════════════════════════════════════════════
# _active_user_text：新转发格式适配 + 旧引用格式回归
# ═══════════════════════════════════════════════════════════════

def test_active_text_plain_message() -> None:
    """普通消息：原样返回，整体参与 @ 解析。"""
    assert _active_user_text("帮我@小林看一下") == "帮我@小林看一下"


def test_active_text_old_quote_format() -> None:
    """旧引用格式（Composer qref）回归不破：只解析「用户说」段。"""
    text = '用户引用了 陆可 的 "旧消息 @小林 你好"，用户说："@陆可 帮我看看"'
    assert _active_user_text(text) == "@陆可 帮我看看"


def test_active_text_forward_template_defensive() -> None:
    """新转发模板串（防御性兜底）：只解析「并配言」后的配言段。"""
    tpl = "用户kai将群A中陆可的消息@小林 旧原文转发了过来，并配言@陆可 帮我看看"
    assert _active_user_text(tpl) == "@陆可 帮我看看"


def test_active_text_forward_empty_comment() -> None:
    """新模板空配言：配言段为空串。"""
    tpl = "用户kai将群A中陆可的消息原文转发了过来，并配言"
    assert _active_user_text(tpl) == ""


# ═══════════════════════════════════════════════════════════════
# resolve_mentions 集成：@ 只在配言段生效（架构 B5 回归）
# ═══════════════════════════════════════════════════════════════

_MEMBERS = [
    MentionMember(agent_id="coordinator", name="项目经理", role="项目经理", coordinator=True),
    MentionMember(agent_id="ux_1", name="小林", role="前端"),
    MentionMember(agent_id="dev_1", name="陆可", role="后端"),
]


def test_mentions_ignore_at_in_forward_original() -> None:
    """转发模板串流入解析器：原文里的 @小林 不触发路由（B5 核心回归）。"""
    tpl = "用户kai将群A中陆可的消息@小林 旧内容转发了过来，并配言帮我看看"
    res = resolve_mentions(tpl, _MEMBERS)
    assert res.worker_ids == ()


def test_mentions_parse_at_in_forward_comment() -> None:
    """转发配言里的 @小林 正常触发直达。"""
    tpl = "用户kai将群A中陆可的消息旧内容转发了过来，并配言@小林 帮我看看"
    res = resolve_mentions(tpl, _MEMBERS)
    assert res.worker_ids == ("ux_1",)


def test_mentions_plain_comment_direct() -> None:
    """新客户端 content=配言原文（无模板包装）：@ 直接生效。"""
    res = resolve_mentions("@小林 帮我看看", _MEMBERS)
    assert res.worker_ids == ("ux_1",)
