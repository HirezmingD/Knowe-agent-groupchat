# [v1.0.13][R3] Atomic CompletionViewV1 wire contract and replay persistence.
"""
contract.py — 出站契约的唯一真源。

前端的 Zod（envelope.ts）是权威，这个文件是它在 Python 侧的镜像：
**每一个出站事件都必须在 EVENT_SPEC 里登记，并在发出前过一遍 validate_outbound()。**

这就是 B-6（STRICT_CONTRACT 覆盖不全）的根治：不是「有些事件会被检查」，
而是「没登记的事件根本发不出去」——发未登记事件 = 立即 ContractViolation。

两类事件（PROTOCOL.md 信封通则）：
  · 引擎级：server 注入 project_id / project_name / ts，并在锁内盖 seq
  · 服务器级：无 seq（白名单：project_created / project_state_changed / project_renamed /
              pong / replay_complete / resync_required / project_directory_required /
              project_directory_restored / 服务器级 error）
              ——前端传输层走旁路，不进水位

⚠ 服务器级 error 与引擎级 error 共享 type="error"，前端靠「有没有 seq」区分。
   所以：能归因到项目的错误 → 走 engine_error（带 project_id + seq）  ← B-3 的修法
        无法归因的（畸形帧、未知指令）→ server_error（无 seq，进全局通知）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


class ContractViolation(Exception):
    """出站事件不符合前端契约——宁可炸在服务端，也不让前端 Zod 拒收后静默丢弃。"""


def now_ts() -> str:
    """ISO 8601 UTC，带 Z 后缀（datetime.utcnow() 在 3.12 已废弃，不用）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ═══════════════════════════════════════════════════════════════
# 一、事件规格表（对照 envelope.ts 逐字段抄写）
# ═══════════════════════════════════════════════════════════════

_STR = str
_INT = int


class Spec:
    __slots__ = ("required", "optional", "has_seq")

    def __init__(
        self,
        required: dict[str, type | tuple[type, ...]],
        optional: dict[str, type | tuple[type, ...]] | None = None,
        *,
        has_seq: bool = True,
    ) -> None:
        self.required = required
        self.optional = optional or {}
        self.has_seq = has_seq


# 引擎级事件都隐含 seq/project_id/ts/project_name，这里只列 payload 独有字段
_ENGINE_COMMON_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "project_id": _STR,
    "seq": _INT,
    "ts": _STR,
}
_ENGINE_COMMON_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "project_name": _STR,
}

# [v1.0.17.x] Visible execution correlation.  These fields are transport
# identity, not business state: every lifecycle/phase/content event may carry
# the same Actor scope while legacy persisted events remain valid without it.
_ACTIVITY_CORRELATION_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "scope_id": _STR,
    "task_id": _STR,
    "attempt_id": _STR,
    "run_id": _STR,
    "completion_id": _STR,
    "channel_id": _STR,
}

# Public work-stage projection.  Optional fields ride on existing events only;
# they are live UI hints derived from observable lifecycle/tool facts.
_VISIBLE_STAGE_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    "phase": _STR,
    "stage": _STR,
    "stage_detail": _STR,
    "stage_state": _STR,
}


def _engine(extra_required: dict[str, Any], extra_optional: dict[str, Any] | None = None) -> Spec:
    return Spec(
        {**_ENGINE_COMMON_REQUIRED, **extra_required},
        {**_ENGINE_COMMON_OPTIONAL, **(extra_optional or {})},
        has_seq=True,
    )


EVENT_SPEC: dict[str, Spec] = {
    # ── 聊天 / 流式 ──
    "agent_thinking": _engine(
        {"agent_id": _STR},
        {**_ACTIVITY_CORRELATION_OPTIONAL, **_VISIBLE_STAGE_OPTIONAL},
    ),
    "stream_delta": _engine(                                             # 字段名 content，不是 text
        {"agent_id": _STR, "content": _STR},
        _ACTIVITY_CORRELATION_OPTIONAL,
    ),
    # [v1.0.23.3] 推理增量（reasoning_content 透传）：瞬时事件，同 stream_delta 不落盘
    "reasoning_delta": _engine(
        {"agent_id": _STR, "content": _STR},
        _ACTIVITY_CORRELATION_OPTIONAL,
    ),
    # [v1.0.23.3] 辅助 LLM 提取的四方向建议：瞬时事件，不落盘（用户确认 D-6）
    "suggestions": _engine(
        {"agent_id": _STR, "items": list},
        _ACTIVITY_CORRELATION_OPTIONAL,
    ),
    "stream_reset": _engine(                                             # legacy stream reset remains replayable
        {"agent_id": _STR},
        _ACTIVITY_CORRELATION_OPTIONAL,
    ),
    # [v0.36] files：本轮 Worker 产出的文件清单（safe_write_file / copy_external_file 的产物）。
    #   可选，纯附载——**不改变 content 正文**。前端据此在气泡下渲染文件卡片、点开预览。
    #   这里只校验它是 list；元素的内层形状由前端 Zod（envelope.ts ProducedFileSchema）兜底，
    #   且前端 schema 对未知字段放行（passthrough），后端多带字段不会让整条 message 被拒收。
    "message": _engine(
        {"agent_id": _STR, "content": _STR},
        {
            "files": list,
            # [v1.0.23.3] 推理全文 + 思考耗时（agent 层随 message 落定；可选，旧事件兼容）
            "reasoning": _STR,
            "reasoning_seconds": (int, float),
            # Wave 5: deterministic CompletionEvent projection identity.  These fields
            # are optional so legacy chat messages remain wire-compatible.
            "event_id": _STR,
            "idempotency_key": _STR,
            "completion_id": _STR,
            "task_id": _STR,
            "attempt_id": _STR,
            "status": _STR,
            "terminal": bool,
            "reason": _STR,
            "gaps": list,
            "gap_details": list,
            "next_actions": list,
            "version": _INT,
            "delivery": dict,
            "metadata": dict,
            **_ACTIVITY_CORRELATION_OPTIONAL,
            **_VISIBLE_STAGE_OPTIONAL,
        },
    ),  # content 可为空串
    "tool_gen": _engine(                                                 # 字段名 tool_name，不是 tool
        {"agent_id": _STR, "tool_name": _STR},
        {**_ACTIVITY_CORRELATION_OPTIONAL, **_VISIBLE_STAGE_OPTIONAL},
    ),
    "tool_start": _engine({"agent_id": _STR}, _ACTIVITY_CORRELATION_OPTIONAL),
    "tool_complete": _engine({"agent_id": _STR}, _ACTIVITY_CORRELATION_OPTIONAL),

    # ── 闸门 / 审批 ──
    "approval_card": _engine(
        {"tool": _STR, "card_id": _STR, "card": dict},
        {"agent_id": _STR},
    ),
    "approval_resolved": _engine({"card_id": _STR, "resolution": _STR}),

    # ── 团队 / 状态 ──
    "agents_created": _engine({"agent_id": _STR, "count": _INT, "members": list}),
    # [v0.33 Bug1b] 组队提议没通过（拒绝/超时/取消）→ 明确广播「这些人没进队」。
    #   审批卡为了渲染头像带出过 {id, role, name}，前端可能已乐观注册；
    #   这条事件是撤销的凭据——没有它，被拒的人会赖在花名册面板里当鬼影。
    "agents_rejected": _engine({"agent_id": _STR, "decision": _STR, "members": list}),
    # [v0.9b] 成员被归档（不是彻底删除——他的报告和产出都还在）
    "agent_removed": _engine({"agent_id": _STR, "target_id": _STR}, {"reason": _STR}),
    "instruction_injected": _engine(
        {"agent_id": _STR, "target_id": _STR},
        {"attempt_id": _STR},
    ),
    # TaskEnvelope 预检失败是一个正式的控制面结果，不是 Python 异常文本。
    # tools_knowe 会在任何磁盘写入/Runtime 入队之前发出它，让前端能展示结构化诊断；
    # 未登记会让诊断事件自己触发 ContractViolation，反而掩盖真正的编译错误。
    "task_contract_blocked": _engine(
        {"agent_id": _STR, "target_id": _STR, "diagnostics": list},
        {"code": _STR, "message": _STR},
    ),
    "report_submitted": _engine(
        {"agent_id": _STR, "report_hash": _STR},
        {"attempt_id": _STR, "event_id": _STR, "completion_id": _STR,
         "task_id": _STR, "status": _STR,
         **_ACTIVITY_CORRELATION_OPTIONAL},
    ),
    # [v1.0.17] 任意 agent 回合开始信号（与 agent_idle 对称成对）
    "agent_active": _engine(
        {"agent_id": _STR},
        {"reason": _STR, **_ACTIVITY_CORRELATION_OPTIONAL},
    ),
    # [v0.15] Worker 回合结束信号——不在 STRUCTURAL，不进时间线/快照，只驱动前端状态
    "agent_idle": _engine(
        {"agent_id": _STR},
        {
            "event_id": _STR,
            "completion_id": _STR,
            # P0 control-plane convergence: idle is a projection, never an
            # independent lifecycle fact.  Task-backed projections carry their
            # lineage; direct-turn availability projections explicitly state that
            # no authoritative attempt remains active.
            "task_id": _STR,
            "attempt_id": _STR,
            "status": _STR,
            "terminal": bool,
            "derived": bool,
            "derived_from": _STR,
            **_ACTIVITY_CORRELATION_OPTIONAL,
        },
    ),
    # Single-source terminal/wait projection.  It is durable but not a chat row.
    "completion_status": _engine(
        {
            "event_id": _STR,
            "completion_id": _STR,
            "task_id": _STR,
            "attempt_id": _STR,
            "agent_id": _STR,
            "status": _STR,
            "terminal": bool,
            "reason": _STR,
            "gaps": list,
            "next_actions": list,
        },
        {
            "version": _INT,
            # Added after the first completion-status schema.  Keep it optional so
            # rolling upgrades and persisted v1 projections remain replayable.
            "gap_details": list,
            "files": list,
            "delivery": dict,
            "metadata": dict,
            **_ACTIVITY_CORRELATION_OPTIONAL,
        },
    ),
    # [v1.0.13][R3] One event carries the complete user-facing projection.  It is
    # structural because the rendered result belongs in the conversation snapshot.
    "completion_view_v1": _engine(
        {
            "event_id": _STR,
            "completion_id": _STR,
            "task_id": _STR,
            "attempt_id": _STR,
            "agent_id": _STR,
            "version": _INT,
            "status": _STR,
            "terminal": bool,
            "user_visible": dict,
            "rendered_text": _STR,
            "created_at": _STR,
        },
        {
            "delivery": dict,
            "metadata": dict,
            **_ACTIVITY_CORRELATION_OPTIONAL,
        },
    ),
    "recovery_notice": _engine({"message": _STR}, {"details": dict}),

    # ── 引擎级 error（有 project_id + seq）──
    "error": _engine(
        {"message": _STR},
        {"agent_id": _STR, **_ACTIVITY_CORRELATION_OPTIONAL, **_VISIBLE_STAGE_OPTIONAL},
    ),

    # ── 用户回显（服务器级发射，但带 seq、进 ring）──
    "user_echo": Spec(
        {"project_id": _STR, "content": _STR, "seq": _INT},
        {"client_msg_id": (str, type(None)), "ts": _STR},
        has_seq=True,
    ),

    # ── 快照（带 seq，进 ring）──
    "state_snapshot": Spec(
        {"last_seq": _INT, "agents": list, "conversation": list, "seq": _INT},
        {"project_id": _STR, "pending_card": (dict, type(None)), "ts": _STR,
         },
        has_seq=True,
    ),

    # ══ 以下是无 seq 白名单（前端传输层旁路，绝不能带 seq）══
    "project_created": Spec(
        {"project_id": _STR},
        {"project_name": _STR, "unread_count": _INT, "project_dir": _STR,
         # [v0.16] 乐观前端 id → canonical id 的建群关联；server 已长期在发送，
         # 这里补齐契约，strict 模式下也不能把合法建群广播误判为越界字段。
         "request_project_id": _STR,
         "members": list,           # [v0.8d #1] 开机第一帧就把花名册带过去
         # [v0.13 卡片] 若该项目当前目录失效，握手/冷启动时随 project_created 一并告知，
         #   前端据此在侧边栏亮红字「未处理事项」并允许重开目录恢复卡片（内层形状见
         #   envelope.ts ProjectDirectoryInfoSchema：previous_dir / reason / request_id）。
         "directory_required": dict},
        has_seq=False,
    ),
    # [v0.44.8] 群聊列表偏好是 Harness 持久态，不属于某条聊天时间线，因此不占 seq。
    # 前端收到后可立即多窗口对账；旧前端若还没登记这两类事件，server 还会补发一条
    # project_created 作为兼容刷新信号，再通过本机 HTTP 读取同一份权威状态。
    "project_state_changed": Spec(
        {
            "project_id": _STR,
            "pinned": bool,
            "muted": bool,
            "folded": bool,
            "pinned_at": _INT,
        },
        {"project_name": _STR},
        has_seq=False,
    ),
    "project_renamed": Spec(
        {
            "project_id": _STR,
            "project_name": _STR,
            "old_project_name": _STR,
        },
        {"project_dir": _STR},
        has_seq=False,
    ),
    "replay_complete": Spec(
        {"last_seq": _INT},
        {"project_id": _STR, "note": _STR, "unread_count": _INT},
        has_seq=False,
    ),
    "resync_required": Spec(
        {"last_seq": _INT},
        {"message": _STR, "project_id": _STR},
        has_seq=False,
    ),
    # v0.13：项目根目录失效时的系统弹窗契约（独立窗口，不进聊天时间线）。
    "project_directory_required": Spec(
        {
            "project_id": _STR, "project_name": _STR, "previous_dir": _STR,
            "reason": _STR, "request_id": _STR,
        },
        {"message": _STR, "can_cancel": bool},
        has_seq=False,
    ),
    "project_directory_restored": Spec(
        {"project_id": _STR, "project_dir": _STR, "request_id": _STR},
        {"message": _STR},
        has_seq=False,
    ),
    "project_delete_progress": Spec(
        {
            "operation_id": _STR,
            "project_id": _STR,
            "phase": _STR,
            "message": _STR,
            "elapsed_ms": _INT,
        },
        {},
        has_seq=False,
    ),
    # Project token accounting is a point-to-point query response.  It must not
    # consume a chat sequence number or enter the durable conversation stream.
    "token_usage_res": Spec(
        {
            "project_id": _STR,
            "daily": list,
            "totals": dict,
            "by_agent": list,
            "by_model": list,
            "current_model": _STR,
            "pricing": dict,
        },
        {"request_id": _INT, "error": _STR},
        has_seq=False,
    ),
    "pong": Spec({}, {}, has_seq=False),
}

# 服务器级 error：无 seq、无 project_id，进前端全局通知通道。
# 与引擎级 error 同 type，靠「有没有 seq」区分——所以单独一条规格。
SERVER_ERROR_SPEC = Spec({"message": _STR}, {"ts": _STR}, has_seq=False)

# 结构事件：进 UI 时间线，也进快照的 conversation（envelope.ts STRUCTURAL_EVENT_TYPES）
STRUCTURAL_EVENT_TYPES: frozenset[str] = frozenset({
    "message",
    "completion_view_v1",
    "approval_card",
    "approval_resolved",
    "agents_created",
    "agent_removed",          # [v0.9b] 进时间线，也进快照（「XX 已离开项目」那条系统行）
    "instruction_injected",
    "report_submitted",
    "error",
    "recovery_notice",
    "user_echo",
})

# Durable control projections that must survive restart/replay but must not be folded
# into the visible chat conversation.
PERSISTABLE_EVENT_TYPES: frozenset[str] = STRUCTURAL_EVENT_TYPES | frozenset({
    "completion_status",
    "agent_idle",
    "task_contract_blocked",
})

# 无 seq 白名单（PROTOCOL.md）
NO_SEQ_EVENT_TYPES: frozenset[str] = frozenset({
    "project_created",
    "project_state_changed",
    "project_renamed",
    "pong",
    "replay_complete",
    "resync_required",
    "project_directory_required",
    "project_directory_restored",
    "project_delete_progress",
    "token_usage_res",
})

RESOLUTIONS: frozenset[str] = frozenset({"approved", "rejected", "timeout", "cancelled"})


# ═══════════════════════════════════════════════════════════════
# 二、校验
# ═══════════════════════════════════════════════════════════════

def _check(event: dict[str, Any], spec: Spec, label: str) -> None:
    # seq 的有无先查——它是前端水位的命根子，错在这里比错在别处贵得多
    if spec.has_seq and "seq" not in event:
        raise ContractViolation(f"{label}: 该事件必须带 seq")
    if not spec.has_seq and "seq" in event:
        raise ContractViolation(f"{label}: 无 seq 白名单事件不得带 seq（前端会误算水位）")

    for key, typ in spec.required.items():
        if key not in event:
            raise ContractViolation(f"{label}: 缺必填字段 {key!r} — {event}")
        if not isinstance(event[key], typ):
            raise ContractViolation(
                f"{label}: 字段 {key!r} 类型应为 {typ}，实为 {type(event[key])} — {event}"
            )
    for key, value in event.items():
        if key in ("type",) or key in spec.required:
            continue
        if key not in spec.optional:
            raise ContractViolation(f"{label}: 出现未登记字段 {key!r}（契约外字段一律拒发） — {event}")
        if not isinstance(value, spec.optional[key]):
            raise ContractViolation(
                f"{label}: 可选字段 {key!r} 类型应为 {spec.optional[key]}，实为 {type(value)}"
            )


def validate_outbound(event: dict[str, Any]) -> None:
    """发出去之前的最后一道门。任何不合规 → ContractViolation。"""
    etype = event.get("type")
    if not isinstance(etype, str):
        raise ContractViolation(f"事件没有 type: {event}")

    # 服务器级 error：靠「没有 seq」识别
    if etype == "error" and "seq" not in event:
        _check(event, SERVER_ERROR_SPEC, "server_error")
        return

    spec = EVENT_SPEC.get(etype)
    if spec is None:
        raise ContractViolation(
            f"未登记的事件类型 {etype!r} —— 前端 Zod 会拒收。"
            f"新事件必须先进 EVENT_SPEC（和 envelope.ts）再写代码。"
        )
    _check(event, spec, etype)

    # 逐事件的额外语义约束
    if etype == "approval_resolved" and event["resolution"] not in RESOLUTIONS:
        raise ContractViolation(f"approval_resolved.resolution 非法: {event['resolution']!r}")
    if etype == "approval_card":
        _check_card(event["card"])


def _check_card(card: dict[str, Any]) -> None:
    """
    审批卡内层形状必须落在 ProposeAgentsCard | ProposeNextCard | ProposeProjectCard |
    ProposeRemoveCard 之一。

    ★ [v0.9b] 派活卡和移除卡都带 target_id —— 靠 **有没有 instruction** 区分。
      顺序很要紧：先判「target_id + instruction」（派活），再判「只有 target_id」（移除）。
      反过来的话，派活卡会被当成移除卡放行，而 instruction 字段悄悄溜进一张
      前端不认得的卡里。
    """
    if card.get("status") != "pending_approval":
        raise ContractViolation(f"card.status 必须是 'pending_approval'，实为 {card.get('status')!r}")
    for key in ("expires_at", "approval_id"):
        if not isinstance(card.get(key), str):
            raise ContractViolation(f"card 缺字符串字段 {key!r}: {card}")

    if "project_name" in card:   # [v0.5] 建群卡（知知专用）
        if not isinstance(card["project_name"], str) or not card["project_name"].strip():
            raise ContractViolation(f"建群卡的 project_name 必须是非空字符串: {card}")
    elif "proposed" in card:  # 组队卡
        proposed = card["proposed"]
        if not isinstance(proposed, list):
            raise ContractViolation("card.proposed 必须是数组")
        for item in proposed:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) \
                    or not isinstance(item.get("role"), str):
                raise ContractViolation(f"card.proposed 元素必须是 {{id, role}}: {item}")
    elif "target_id" in card and "instruction" in card:  # 派活卡
        if not isinstance(card.get("target_id"), str) or not isinstance(card.get("instruction"), str):
            raise ContractViolation(f"派活卡需要 target_id + instruction: {card}")
    elif "target_id" in card:  # [v0.9b] 移除卡（有 target_id、没有 instruction）
        if not isinstance(card.get("target_id"), str) or not card["target_id"].strip():
            raise ContractViolation(f"移除卡的 target_id 必须是非空字符串: {card}")
        if "reason" in card and not isinstance(card["reason"], str):
            raise ContractViolation(f"移除卡的 reason 必须是字符串: {card}")
    else:
        raise ContractViolation(
            f"审批卡不是组队卡（proposed）、派活卡（target_id + instruction）、"
            f"移除卡（target_id）或建群卡（project_name）: {card}"
        )

    allowed = {"status", "expires_at", "approval_id", "proposed", "target_id",
               "instruction", "card_id", "recovered",
               "project_name",   # [v0.5] 建群卡
               "note",           # [v0.30 修账] v0.28 的派活卡备注——前端一直在渲染它
                                 #   （ApprovalCard.targetOf 读 card.note），这份白名单
                                 #   却没登记：带 note 的卡会在 validate_outbound 被拒发。
               "reason",          # [v0.9b] 移除卡
               # Relay root-approval extension; all are optional so legacy cards keep
               # their exact shape when the feature is disabled.
               "first_step_id"}
    extra = set(card) - allowed
    if extra:
        raise ContractViolation(f"审批卡出现契约外字段 {extra}")


def iter_registered_types() -> Iterable[str]:
    return EVENT_SPEC.keys()
