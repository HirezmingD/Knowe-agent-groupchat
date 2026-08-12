"""群聊 ``@提及`` 的纯解析器。

这一层只做「文本里提到了谁」：不碰 WebSocket、不碰引擎，也不触发任何回合。
把它从 server/engine 里抽出来有两个好处：

* 花名册仍是身份真源，调用方只需把当前在册成员传进来；
* 解析器是纯函数，可以覆盖中文名、英文名、agent id、唯一角色名和多提及测试。

未知或歧义提及一律不猜。解析不到 Worker 时，调用方按普通群聊交给总管。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

__all__ = ["MentionMember", "MentionResolution", "resolve_mentions"]


@dataclass(frozen=True, slots=True)
class MentionMember:
    """解析所需的最小成员身份。"""

    agent_id: str
    name: str
    role: str = ""
    coordinator: bool = False


@dataclass(frozen=True, slots=True)
class MentionResolution:
    """一条群消息的路由结果。Worker 顺序与花名册顺序一致。"""

    worker_ids: tuple[str, ...]
    coordinator: bool = False


# 「类似指向 coordinator」的稳定别名。项目自己的总管显示名 / id / role 会在运行时再补。
_COORDINATOR_ALIASES = frozenset({
    "主管", "总管", "项目经理", "项目主管", "项目总管", "协调者", "coordinator",
})

# 这些角色名太泛，拿来 @ 会造成误投；只有有区分度且在当前队伍唯一的角色才可作别名。
_GENERIC_ROLES = frozenset({"", "成员", "worker", "agent", "总管", "项目经理", "主管"})

# 防止把 foo@shiloh.com 当成提及；中文正文紧贴 @ 是允许的（「请@Shiloh查一下」）。
_EMAIL_LOCAL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-@")
_ASCII_ALIAS_TAIL = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_QUOTED_USER_MARKER = '，用户说："'
# [v1.0.23.1] 转发 LLM 模板标记（server.build_forward_template 同款）：
# 「…转发了过来，并配言{附言}」——@ 解析只在配言段做，原文里的 @ 绝不触发路由。
_FORWARD_USER_MARKER = "转发了过来，并配言"


def _active_user_text(content: str) -> str:
    """只在「用户自己说的话」段解析 @，避免旧消息/被转发原文里的 @误路由。

    普通消息原样返回。这里不做通用自然语言引号解析：用户可能确实在正文里用引号点名；
    只识别前端已经存在、格式稳定的结构化包装：

    * 引用（Composer qref）：``用户引用了 …，用户说："…"`` → 解析「用户说」段；
    * 转发（[v1.0.23.1] 防御性）：``…转发了过来，并配言{附言}`` → 解析配言段。
      正常新客户端 content 就是配言原文（不匹配任何包装，整体解析）；这里兜住
      「模板串意外流入」的旧客户端/历史数据场景（架构 B7）。
    """
    text = content if isinstance(content, str) else ""
    if text.startswith("用户引用了 ") and _QUOTED_USER_MARKER in text:
        current = text.rsplit(_QUOTED_USER_MARKER, 1)[1]
        return current[:-1] if current.endswith('"') else current
    if _FORWARD_USER_MARKER in text:
        return text.split(_FORWARD_USER_MARKER, 1)[1]
    return text


def _clean_alias(value: str) -> str:
    alias = (value or "").strip()
    while alias.startswith("@"):
        alias = alias[1:].lstrip()
    return alias


def _mentioned_aliases(text: str, aliases: Iterable[str]) -> set[str]:
    """返回文本中实际命中的 alias(casefold)，每个 ``@`` 位置只取**最长匹配**。

    最长匹配解决中文前缀名：花名册同时有「小林」「小林子」时，``@小林子`` 只能投给
    后者；同时仍允许 ``@小林帮我``（没有更长已知名字时，「帮我」就是紧贴的正文）。
    英文/agent-id 继续要求尾部边界，避免 ``@Ann`` 命中 ``@Anna``。
    """

    cleaned = tuple(dict.fromkeys(
        alias for raw in aliases if (alias := _clean_alias(raw))
    ))
    if not cleaned:
        return set()

    matched: set[str] = set()
    for idx, char in enumerate(text):
        if char != "@":
            continue
        if idx > 0 and text[idx - 1] in _EMAIL_LOCAL_CHARS:
            continue

        candidates: list[str] = []
        start = idx + 1
        for alias in cleaned:
            match = re.match(re.escape(alias), text[start:], re.IGNORECASE)
            if match is None:
                continue
            end = start + match.end()
            if end < len(text) and alias[-1] in _ASCII_ALIAS_TAIL:
                if text[end] in _ASCII_ALIAS_TAIL:
                    continue
            candidates.append(alias)

        if not candidates:
            continue
        longest = max(len(alias) for alias in candidates)
        matched.update(alias.casefold() for alias in candidates if len(alias) == longest)
    return matched


def resolve_mentions(content: str, members: Iterable[MentionMember]) -> MentionResolution:
    """按当前花名册解析群聊 @提及。

    可识别：
      * coordinator 的固定中文/英文别名、显示名、id、role；
      * Worker 的显示名、agent id；
      * 当前队伍中唯一且不泛化的角色名（例如只有一位「前端」时可 ``@前端``）。

    同名/同角色出现歧义时不路由给任何一个 Worker，避免静默发错人。
    """

    text = _active_user_text(content)
    roster = tuple(members)
    if not text or "@" not in text or not roster:
        return MentionResolution(())

    coordinators = [m for m in roster if m.coordinator]
    workers = [m for m in roster if not m.coordinator]

    coordinator_aliases = set(_COORDINATOR_ALIASES)
    for member in coordinators:
        coordinator_aliases.update({member.agent_id, member.name, member.role})
    coordinator_aliases = {_clean_alias(a) for a in coordinator_aliases if _clean_alias(a)}
    coordinator_aliases_folded = {alias.casefold() for alias in coordinator_aliases}

    # alias(casefold) → 原始 alias + 可能目标。先收集再判唯一，绝不靠「第一个命中」猜人。
    alias_targets: dict[str, tuple[str, set[str]]] = {}

    def add_worker_alias(alias_value: str, agent_id: str) -> None:
        alias = _clean_alias(alias_value)
        if not alias or alias.casefold() in coordinator_aliases_folded:
            return
        key = alias.casefold()
        if key not in alias_targets:
            alias_targets[key] = (alias, set())
        alias_targets[key][1].add(agent_id)

    for member in workers:
        add_worker_alias(member.agent_id, member.agent_id)
        add_worker_alias(member.name, member.agent_id)

    # 唯一角色可以当自然别名；重复角色不猜。
    role_owners: dict[str, list[MentionMember]] = {}
    generic_roles_folded = {role.casefold() for role in _GENERIC_ROLES}
    for member in workers:
        role = _clean_alias(member.role)
        if role.casefold() in generic_roles_folded:
            continue
        role_owners.setdefault(role.casefold(), []).append(member)
    for owners in role_owners.values():
        if len(owners) == 1:
            add_worker_alias(owners[0].role, owners[0].agent_id)

    mentioned = _mentioned_aliases(
        text,
        (*coordinator_aliases, *(alias for alias, _targets in alias_targets.values())),
    )
    coordinator_hit = bool(mentioned & coordinator_aliases_folded)

    matched_ids: set[str] = set()
    for alias, targets in alias_targets.values():
        if len(targets) == 1 and alias.casefold() in mentioned:
            matched_ids.update(targets)

    ordered = tuple(member.agent_id for member in workers if member.agent_id in matched_ids)
    return MentionResolution(worker_ids=ordered, coordinator=coordinator_hit)
