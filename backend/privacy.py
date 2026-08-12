"""
privacy.py — 用户可见文本的硬脱敏层。

Prompt 约束只能降低模型犯错的概率，不能作为安全边界。本模块只处理“给用户看的自然语言”，
结构化字段（agent_id / target_id / report_hash 等）保持原样，供前端路由和内部追溯使用。

硬规则：
  · 成员内部 id（fe_1 / pm_1 / …）在自然语言中替换为成员名字；找不到名字时替换为“内部成员”。
  · handoffs/...、report-XX-<id>-....md、instruction-XX-<id>-....md、.approval-XX.md
    这类内部交接路径/文件名替换为“内部交接文件”。
  · user_echo 不改——用户自己输入的原文必须忠实显示；守卫只拦系统/模型出站文本。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

# 与 tools_knowe.KNOWN_ROLES 的前缀保持同步。这里故意不 import tools_knowe，避免循环依赖。
_AGENT_PREFIXES = (
    "fe", "be", "pm", "qa", "ux", "da", "devops", "sec", "ml", "mobile",
    "game", "gis", "mkt", "fin", "hc", "edu", "ar", "sup", "sre", "db",
    "arch", "writer", "media", "legal",
)
_PREFIX_ALT = "|".join(sorted((re.escape(p) for p in _AGENT_PREFIXES), key=len, reverse=True))
_AGENT_ID_RE = re.compile(rf"(?<![\w])(?:{_PREFIX_ALT})_\d+(?![\w])", re.IGNORECASE)
_PROJECT_ID_RE = re.compile(r"(?<![\w])p_[a-z0-9][a-z0-9_-]{5,}(?![\w])", re.IGNORECASE)
_APPROVAL_ID_RE = re.compile(r"(?<![\w])ap_[a-z0-9][a-z0-9_-]{5,}(?![\w])", re.IGNORECASE)

# 内部交接路径。先吃完整绝对路径，再吃从 handoffs 开始的相对路径；
# 避免只把 ``D:\项目\handoffs\x.md`` 的后半截替换掉、仍把根目录残留在对话里。
_ABSOLUTE_HANDOFF_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|/)[^\s<>\"'`，。！？；：）】}\]]*?"
    r"handoffs[\\/][^\s<>\"'`，。！？；：）】}\]]+"
)
_HANDOFF_PATH_RE = re.compile(
    r"(?i)(?<![\w])handoffs[\\/][^\s<>\"'`，。！？；：）】}\]]+"
)
_INTERNAL_FILE_RE = re.compile(
    rf"(?i)(?<![\w])(?:report|instruction)-\d{{1,5}}-(?:{_PREFIX_ALT})_\d+-"
    r"[^\s<>\"'`，。！？；：）】}\]]*?\.md"
)
_APPROVAL_FILE_RE = re.compile(r"(?i)(?<![\w])\.approval-\d{1,5}\.md")


def sanitize_text(text: str, id_to_name: Mapping[str, str] | None = None) -> str:
    """返回可安全展示给用户的文本。输入不是字符串时由调用方决定是否处理。"""
    if not text:
        return text

    result = text
    # 先吃掉路径，避免路径里的 id 被替成名字后留下半截内部文件名。
    result = _ABSOLUTE_HANDOFF_PATH_RE.sub("内部交接文件", result)
    result = _HANDOFF_PATH_RE.sub("内部交接文件", result)
    result = _INTERNAL_FILE_RE.sub("内部交接文件", result)
    result = _APPROVAL_FILE_RE.sub("内部交接文件", result)

    # 平台级内部标识也属于硬约束；即便当前没有 ProjectEngine 提供花名册，
    # Hub 的默认守卫仍能把这些名字转换成用户可理解的称呼。
    names = {
        "coordinator": "项目经理",
        "zinnia": "知知",
        "__platform__": "平台会话",
    }
    names.update({
        str(agent_id): str(name).strip()
        for agent_id, name in (id_to_name or {}).items()
        if isinstance(agent_id, str) and isinstance(name, str) and name.strip()
    })
    # 已知 id 用名字替换，最长优先，避免前缀碰撞。
    for agent_id in sorted(names, key=len, reverse=True):
        name = names[agent_id]
        result = re.sub(
            rf"(?<![\w]){re.escape(agent_id)}(?![\w])",
            lambda _m, n=name: n,
            result,
            flags=re.IGNORECASE,
        )

    # 未知但形状明确的成员 id 仍不得漏出。
    result = _AGENT_ID_RE.sub("内部成员", result)
    result = _APPROVAL_ID_RE.sub("当前审批", result)
    result = _PROJECT_ID_RE.sub("当前项目", result)

    # No ordinary-language cleanup belongs here.  The privacy boundary ends after
    # exact internal identifiers and internal handoff paths have been redacted.
    return result


def sanitize_event(
    event: dict[str, Any], id_to_name: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """
    脱敏一条出站事件。只触碰用户可见自然语言，不改任何结构化内部字段。

    返回新 dict；调用方可以安全地把原 payload 留作内部逻辑使用。
    """
    etype = event.get("type")
    if etype == "user_echo":
        return dict(event)

    out = copy.deepcopy(event)

    if etype in ("message", "stream_delta", "reasoning_delta") and isinstance(out.get("content"), str):
        out["content"] = sanitize_text(out["content"], id_to_name)

    # [v1.0.23.3] 推理全文也是 LLM 输出 → 同样逐字脱敏（铁律：LLM 输出不进公网）
    if etype == "message" and isinstance(out.get("reasoning"), str):
        out["reasoning"] = sanitize_text(out["reasoning"], id_to_name)

    # [v1.0.23.3] suggestions 卡片文字是辅助 LLM 输出 → 同样脱敏
    if etype == "suggestions" and isinstance(out.get("items"), list):
        cleaned_items: list[dict[str, Any]] = []
        for item in out["items"]:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            if isinstance(item.get("title"), str):
                item["title"] = sanitize_text(item["title"], id_to_name)
            if isinstance(item.get("sub"), str):
                item["sub"] = sanitize_text(item["sub"], id_to_name)
            cleaned_items.append(item)
        out["items"] = cleaned_items

    if etype in (
        "error", "recovery_notice", "resync_required", "project_directory_required",
        "project_directory_restored",
    ) and isinstance(out.get("message"), str):
        out["message"] = sanitize_text(out["message"], id_to_name)

    if etype == "approval_card" and isinstance(out.get("card"), dict):
        card = out["card"]
        for key in ("instruction", "reason", "message"):
            if isinstance(card.get(key), str):
                card[key] = sanitize_text(card[key], id_to_name)

    if etype == "state_snapshot" and isinstance(out.get("conversation"), list):
        out["conversation"] = [
            sanitize_event(item, id_to_name) if isinstance(item, dict) else item
            for item in out["conversation"]
        ]
        pending = out.get("pending_card")
        if isinstance(pending, dict):
            for key in ("instruction", "reason", "message"):
                if isinstance(pending.get(key), str):
                    pending[key] = sanitize_text(pending[key], id_to_name)

    return out


def sanitize_events(
    events: list[dict[str, Any]], id_to_name: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    return [sanitize_event(event, id_to_name) for event in events]


__all__ = ["sanitize_text", "sanitize_event", "sanitize_events"]
