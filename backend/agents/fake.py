"""
fake.py — Fake agent 档（零 token，联调专用）。

两条剧本（KNOWE_SCRIPT 切换）：

  simple —— 纯消息往返，不碰审批：
      agent_thinking → stream_delta × N → message

  full   —— 全链路（默认）：
      agent_thinking → stream_delta × N → message（"我需要组建团队"）
      → propose_agents 卡 ──等审批──┬─ approved  → agents_created
                                    │              → propose_next 卡 ──等审批──┬─ approved → instruction_injected
                                    │                                          │            → （干活）→ report_submitted
                                    │                                          │            → message（总结）
                                    │                                          └─ 否则 → message（说明为什么没派活）
                                    └─ rejected/timeout → message（说明为什么没组队）

被取消（用户发了新消息）时抛 ApprovalCancelled，引擎那边优雅收摊——
不会留一张永远转圈的卡，也不会硬着头皮把后半段演完。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from ..config import CONFIG
from ..gate import ApprovalCancelled, Gate
from .base import Emit, Turn

COORDINATOR = "coordinator"

# 提议的成员：id 前缀要和前端的角色模板对得上（fe/be/pm/qa/ux）
DEFAULT_TEAM: list[dict[str, str]] = [
    {"id": "fe_1", "role": "前端"},
    {"id": "be_1", "role": "后端"},
]


def _chunks(text: str, size: int = 6) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class FakeAgent:
    """确定性剧本，不调任何外部 API。"""

    def __init__(self, script: str | None = None) -> None:
        self.script = (script or CONFIG.script).lower()

    async def run_turn(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        if self.script == "simple":
            await self._simple(turn, emit)
        else:
            await self._full(turn, emit, gate)

    # ═══════════════════════════════════════════════════════════
    # 剧本一：简化（纯消息往返）
    # ═══════════════════════════════════════════════════════════
    async def _simple(self, turn: Turn, emit: Emit) -> None:
        reply = f"收到：「{turn.content}」。这是 Fake 档的简化剧本，不触发审批。"
        await self._say(emit, COORDINATOR, reply)

    # ═══════════════════════════════════════════════════════════
    # 剧本二：完整（全链路）
    # ═══════════════════════════════════════════════════════════
    async def _full(self, turn: Turn, emit: Emit, gate: Gate) -> None:
        await self._say(
            emit, COORDINATOR,
            f"收到：「{turn.content}」。这件事我一个人做不完，先组个团队——请你确认。",
        )

        # ── 1. 组队审批 ──
        try:
            decision = await gate.propose(
                tool="propose_agents",
                agent_id=COORDINATOR,
                card_body={"proposed": list(DEFAULT_TEAM)},
            )
        except ApprovalCancelled:
            raise  # 引擎会收摊，不在这里画蛇添足

        if decision != "approved":
            await self._say(emit, COORDINATOR, _decline_text("组队", decision))
            return

        members = list(DEFAULT_TEAM)
        await emit({
            "type": "agents_created",
            "agent_id": COORDINATOR,
            "count": len(members),
            "members": members,
        })
        await asyncio.sleep(CONFIG.fake_think_delay_s)

        # ── 2. 派活审批 ──
        target = members[0]
        instruction = f"把「{turn.content}」拆成第一版方案，先出结构，再出细节。"
        try:
            decision = await gate.propose(
                tool="propose_next",
                agent_id=COORDINATOR,
                card_body={"target_id": target["id"], "instruction": instruction},
            )
        except ApprovalCancelled:
            raise

        if decision != "approved":
            await self._say(emit, COORDINATOR, _decline_text("派活", decision))
            return

        # ── 3. 干活 ──
        await emit({
            "type": "instruction_injected",
            "agent_id": COORDINATOR,
            "target_id": target["id"],
        })
        await asyncio.sleep(CONFIG.fake_work_delay_s)

        report = f"{target['id']} 的第一版方案：围绕「{turn.content}」给出结构与要点。"
        await emit({
            "type": "report_submitted",
            "agent_id": target["id"],
            "report_hash": hashlib.sha256(report.encode()).hexdigest()[:16],
        })
        await self._say(emit, target["id"], report)
        await self._say(emit, COORDINATOR, "方案已经交上来了，你看看要不要改。")

    # ═══════════════════════════════════════════════════════════
    # 说一句话 = thinking → deltas → message 收尾
    # ═══════════════════════════════════════════════════════════
    async def _say(self, emit: Emit, agent_id: str, text: str) -> None:
        await emit({"type": "agent_thinking", "agent_id": agent_id})
        await asyncio.sleep(CONFIG.fake_think_delay_s)

        for chunk in _chunks(text):
            await emit({"type": "stream_delta", "agent_id": agent_id, "content": chunk})
            await asyncio.sleep(CONFIG.fake_delta_delay_s)

        await emit({"type": "message", "agent_id": agent_id, "content": text})


def _decline_text(what: str, decision: str) -> str:
    reason = {
        "rejected": "你拒绝了",
        "timeout": "等太久没等到你的确认（超时自动撤回）",
        "cancelled": "被新的消息取消了",
    }.get(decision, decision)
    return f"{what}没有进行——{reason}。需要的话再跟我说一声。"


__all__ = ["FakeAgent", "DEFAULT_TEAM", "COORDINATOR"]
