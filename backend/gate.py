"""
gate.py — 审批闸门。

★ BUG-2（控制/工作线程共用队列，approve 可能被 worker 抢走）的**结构性根治**：
  旧版把 approve 塞进和用户消息同一条队列，谁先 get() 到就归谁——worker 正在跑一个回合时
  把控制指令吃掉，审批就石沉大海。

  新设计里**审批根本不走队列**：
    · 提议时 gate 建一个 asyncio.Future，记在 self._pending[card_id]
    · worker 那边 `await fut`（挂起，不占队列）
    · server 收到 approve/reject → 直接 `gate.resolve(card_id, ...)` → set_result
  没有共享队列，就没有「抢」这回事。不是把 bug 修好，是把 bug 的生存空间拆掉。

两条硬保证：
  · **恰好一次解决**：一张卡只出站一条 approval_resolved。approve / reject / timeout /
    cancelled 四条路径全部收束到 _settle()，_settle 用 pop 保证只执行一次。
  · **默认永不自动超时**；只有用户显式配置有限秒数时才产生 timeout。

前端有「首个解决为准」的幂等防线；后端这边根本不制造第二条——两边都不出错才叫可靠。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from .config import CONFIG
from .hub import Hub

log = logging.getLogger("knowe.gate")

Resolution = Literal["approved", "rejected", "timeout", "cancelled"]


class ApprovalCancelled(Exception):
    """挂起的审批被取消（用户发了新消息 / 引擎停机）——worker 应当优雅收摊。"""


NEVER_EXPIRES_AT = "9999-12-31T23:59:59Z"


def iso_in(seconds: float | None) -> str:
    if seconds is None:
        return NEVER_EXPIRES_AT
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)) \
        .isoformat().replace("+00:00", "Z")


class _Pending:
    __slots__ = ("card_id", "tool", "agent_id", "card", "future")

    def __init__(self, card_id: str, tool: str, agent_id: str,
                 card: dict[str, Any], future: "asyncio.Future[Resolution]") -> None:
        self.card_id = card_id
        self.tool = tool
        self.agent_id = agent_id
        self.card = card
        self.future = future


class Gate:
    """一个项目一个闸门。"""

    def __init__(self, hub: Hub, project_id: str) -> None:
        self.hub = hub
        self.project_id = project_id
        self._pending: dict[str, _Pending] = {}
        self._recovery_tasks: set[asyncio.Task[None]] = set()
        # [v0.30 Bug2/3] 卡落定那一刻要通知的人（引擎注册）。
        #   用途只有一个：**取消这张卡关联的 in-flight 反馈调整**——
        #   卡都没了，还在替它改指令的那次 LLM 调用就是幽灵，必须跟着死。
        #   放在 _settle 里调（恰好一次落定的唯一出海口），四条路径天然全覆盖。
        self.settle_listener: "Any | None" = None

    # ── 查询 ──
    def has_pending(self) -> bool:
        return bool(self._pending)

    def has_pending_tool(self, tool: str) -> bool:
        """[v0.30 Bug3] 此刻有没有**这种工具**的卡还挂着？（propose_next 单张化用它。）"""
        return any(p.tool == tool and not p.future.done()
                   for p in self._pending.values())

    def pending_of(self, card_id: str) -> _Pending | None:
        """[v0.26] 按 id 拿那张还挂着的卡。拿不到 = 它已经落定或压根不存在。"""
        return self._pending.get(card_id)

    async def update_card(self, card_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """
        [v0.26] **原地改一张还挂着的卡**，然后把它重新播一遍。

        ★ 这个方法**碰都不碰 future**——卡还是 pending，倒计时接着走，
          approve / reject 的语义一个字没变。变的只有卡上的字。
          「恰好一次落定」那条硬保证因此完好无损：_settle 仍然是四条路的唯一出海口，
          而这里根本不是一条落定路径。

        为什么重发 `approval_card` 而不是新造一个 `approval_card_updated` 事件：
          · **卡的身份就是 card_id**。同一个 card_id 再来一次 = 「这张卡的内容变了」——
            幂等语义，天然对。
          · 契约一个字不用改（approval_card 早就登记过了）。
          · **回放/快照白拿**：ring 里两条 approval_card，重放时后一条覆盖前一条，
            终态自然正确。要是新造一个事件类型，回放这条路得另外再想一遍。
          · 前端 applyEvent 认 card_id 就地更新 → **item 还在原来那一格** →
            这就是「原地 morph」本身，不用再发明什么。
        """
        pending = self._pending.get(card_id)
        if pending is None or pending.future.done():
            return None
        pending.card.update(patch)

        proj = self.hub.get_or_create(self.project_id)
        if proj.pending_card is not None and proj.pending_card.get("approval_id") == card_id:
            proj.pending_card = pending.card

        await self.hub.emit(self.project_id, {
            "type": "approval_card",
            "agent_id": pending.agent_id,
            "tool": pending.tool,
            "card_id": card_id,
            # ★ 发**副本**。见下面 propose() 里那段注释：卡现在是会变的，
            #   发引用等于让已经出站的那条事件跟着一起变。
            "card": dict(pending.card),
        })
        log.info("[%s] approval %s 卡面已更新（仍在等审批）", self.project_id, card_id)
        return pending.card

    @property
    def pending_cards(self) -> list[dict[str, Any]]:
        return [p.card for p in self._pending.values()]

    def snapshot_pending(self) -> list[tuple[str, str, dict[str, Any]]]:
        """(tool, agent_id, card) 三元组——引擎重启后按此复提（B-4）。"""
        return [(p.tool, p.agent_id, p.card) for p in self._pending.values()]

    @staticmethod
    def _remaining_timeout(card: dict[str, Any]) -> float | None:
        raw = str(card.get("expires_at") or "").strip()
        if not raw or raw == NEVER_EXPIRES_AT:
            return CONFIG.approval_timeout_s
        try:
            expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return max(0.0, (expires - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return CONFIG.approval_timeout_s

    def restore_pending(self, *, tool: str, agent_id: str, card: dict[str, Any]) -> bool:
        """Rebuild one durable approval gate without changing its public identity.

        Approval cards are persisted in the event stream, but their ``Future`` objects
        are necessarily process-local.  Restoring the same ``approval_id`` reconnects
        approve/reject control frames to a live gate after a process restart.  The
        original worker continuation cannot be resurrected, so the recovered gate's
        responsibility is to reach one explicit terminal resolution rather than leave
        a replayed zombie card.
        """

        restored = dict(card)
        card_id = str(restored.get("approval_id") or "").strip()
        if not card_id or card_id in self._pending:
            return False
        restored["status"] = "pending_approval"
        restored["approval_id"] = card_id
        restored["recovered"] = True

        loop = asyncio.get_running_loop()
        future: "asyncio.Future[Resolution]" = loop.create_future()
        self._pending[card_id] = _Pending(card_id, tool, agent_id, restored, future)

        proj = self.hub.get_or_create(self.project_id)
        proj.pending_card = restored

        task = loop.create_task(
            self._await_recovered(card_id, future, self._remaining_timeout(restored)),
            name=f"approval-recovery:{self.project_id}:{card_id}",
        )
        self._recovery_tasks.add(task)
        task.add_done_callback(self._recovery_tasks.discard)
        log.warning("[%s] restored pending approval %s", self.project_id, card_id)
        return True

    async def _await_recovered(
        self,
        card_id: str,
        future: "asyncio.Future[Resolution]",
        timeout_s: float | None,
    ) -> None:
        try:
            if timeout_s is None:
                resolution: Resolution = await asyncio.shield(future)
            elif timeout_s <= 0:
                resolution = "timeout"
            else:
                resolution = await asyncio.wait_for(
                    asyncio.shield(future), timeout=timeout_s,
                )
        except asyncio.TimeoutError:
            resolution = "timeout"
        except asyncio.CancelledError:
            await self._settle(card_id, "cancelled")
            raise
        await self._settle(card_id, resolution)

    # ═══════════════════════════════════════════════════════════
    # worker 侧：提议并挂起，直到有结果
    # ═══════════════════════════════════════════════════════════
    async def propose(
        self,
        *,
        tool: str,
        agent_id: str,
        card_body: dict[str, Any],
        recovered: bool = False,
        timeout_s: float | None = None,
        card_out: dict[str, Any] | None = None,
    ) -> Resolution:
        """
        发一张审批卡并等结果。返回 'approved' / 'rejected' / 'timeout'；
        被取消时抛 ApprovalCancelled（worker 应当结束本回合）。

        card_body：组队卡 {"proposed": [{id, role}, ...]}
                   派活卡 {"target_id": ..., "instruction": ...}

        [v0.26] card_out：传一个空 dict 进来 → 落定时把**最终**的卡体拷回去。

          为什么需要它：`update_card()` 可以在等待期间**原地改掉卡上的 instruction**
          （用户点了「我有新意见」）。可 handle_propose_next 手里攥的还是它自己那份
          局部变量 `instruction` —— 拿它去 commit_handoff_step，就等于**用户改了个寂寞**：
          卡上显示新指令，派下去的还是旧的。
          （这个坑很安静：卡面是对的，用户点确认，然后 Worker 干了件旧活。）

          所以要一条**回程**。用可选出参而不是改返回值：三个 propose 调用点里只有
          派活那个需要它，另外两个（组队/移除）一个字都不用动。
        """
        timeout_s = CONFIG.approval_timeout_s if timeout_s is None else timeout_s
        card_id = f"ap_{uuid.uuid4().hex[:12]}"

        card: dict[str, Any] = {
            "status": "pending_approval",
            "expires_at": iso_in(timeout_s),
            "approval_id": card_id,   # 前端铁律：顶层 card_id == card.approval_id
            **card_body,
        }
        if recovered:
            card["recovered"] = True  # B-4：复提卡标记

        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[Resolution]" = loop.create_future()
        self._pending[card_id] = _Pending(card_id, tool, agent_id, card, fut)

        proj = self.hub.get_or_create(self.project_id)
        proj.pending_card = card

        # ★ B-4：顶层 card_id / agent_id / tool 一个都不能少——复提路径也一样
        #
        # ★ [v0.26] `dict(card)` 是**副本**，不是引用 —— 这一行是这一版加的，
        #   而且是被测试逼出来的。
        #
        #   v0.26 之前，卡出站之后就再没人改过它，发引用没有任何区别。
        #   现在 update_card() 会**就地改**这张卡（用户提了新意见）——
        #   发引用的话，那条**已经出站**的 approval_card 事件会跟着一起变：
        #   谁把它存住了（回放 ring / 快照），谁手里的历史就被追溯性地改写了。
        #   历史里那条「卡最初长什么样」会凭空变成新的。
        #   ——只要有一处是「可变对象出了门还被人攥着」，这类 bug 迟早会长出来。
        await self.hub.emit(self.project_id, {
            "type": "approval_card",
            "agent_id": agent_id,
            "tool": tool,
            "card_id": card_id,
            "card": dict(card),
        })

        try:
            if timeout_s is None:
                resolution: Resolution = await fut
            else:
                resolution = await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            resolution = "timeout"
        except asyncio.CancelledError:
            # 引擎被停 → 这张卡也要有个交代，不能留个永远转圈的倒计时
            await self._settle(card_id, "cancelled")
            raise

        # ★ [v0.26] 把**最终**的卡体交回调用方 —— 它可能在等待期间被 update_card 改过。
        #   放在 _settle 之前：_settle 会把 _pending 那一项 pop 掉。
        if card_out is not None:
            card_out.update(self._pending[card_id].card if card_id in self._pending else {})

        final = await self._settle(card_id, resolution)
        if final == "cancelled":
            raise ApprovalCancelled(card_id)
        return final

    # ═══════════════════════════════════════════════════════════
    # 控制侧：approve / reject / 取消（不经任何队列）
    # ═══════════════════════════════════════════════════════════
    def resolve(self, card_id: str, resolution: Resolution) -> bool:
        """返回 False = 卡不存在或已落终态（幂等，不报错）。"""
        pending = self._pending.get(card_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(resolution)
        return True

    def cancel_all(self, reason: Resolution = "cancelled") -> int:
        """用户发新消息 → 挂起的审批全部作废（§三）。返回作废了几张。"""
        n = 0
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result(reason)
                n += 1
        return n

    async def cancel_all_settled(self, reason: Resolution = "cancelled") -> int:
        """停引擎时同步落定全部卡，避免任务被 cancel 后 UI 永远挂着 pending。"""
        card_ids = [cid for cid, pending in self._pending.items() if not pending.future.done()]
        for card_id in card_ids:
            pending = self._pending.get(card_id)
            if pending is not None and not pending.future.done():
                pending.future.set_result(reason)
        for card_id in card_ids:
            await self._settle(card_id, reason)
        return len(card_ids)

    # ═══════════════════════════════════════════════════════════
    # 恰好一次落定：四条路径的唯一出海口
    # ═══════════════════════════════════════════════════════════
    async def _settle(self, card_id: str, resolution: Resolution) -> Resolution:
        pending = self._pending.pop(card_id, None)   # pop = 只可能执行一次
        if pending is None:
            return resolution

        # [v0.30 Bug2/3] 卡落定 → 它名下的 in-flight 反馈调整立刻作废。
        #   放在这里（而不是 approve/reject/cancel 各自的入口）：_settle 是四条
        #   落定路径的唯一出海口，在这儿挂一次钩，就没有哪条路能漏。
        #   listener 出错不许连累落定——落定是给用户的交代，谁也不能拦它。
        if self.settle_listener is not None:
            try:
                self.settle_listener(card_id)
            except Exception:
                log.exception("[%s] approval %s 的落定回调出错（忽略）",
                              self.project_id, card_id)

        proj = self.hub.get_or_create(self.project_id)
        if proj.pending_card is not None and proj.pending_card.get("approval_id") == card_id:
            proj.pending_card = None

        await self.hub.emit(self.project_id, {
            "type": "approval_resolved",
            "card_id": card_id,
            "resolution": resolution,
        })
        log.info("[%s] approval %s → %s", self.project_id, card_id, resolution)
        return resolution


__all__ = ["Gate", "Resolution", "ApprovalCancelled", "iso_in"]
