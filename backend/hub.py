"""
hub.py — 事件总线：项目注册表 / seq 单点盖号 / ring / 广播 / 客户端管理。

三条铁律（PROTOCOL.md §a）：
  1. **seq 由 server 单点加锁分配，按项目独立递增**——引擎不许自己盖号。
  2. **所有事件广播给所有客户端**（含别的项目的事件、含发送者自己的 user_echo）。
     前端按 project_id 路由后再做水位/去重。
  3. **无 seq 白名单事件绝不盖号**（project_created / pong / replay_complete / resync_required /
     project_directory_required / project_directory_restored / 服务器级 error），否则前端水位会被污染。

BUG-1 的根治：剪枝死连接一律用 `ws.state is State.OPEN`。
  新版 websockets（≥14）没有 `.open` 属性——`getattr(c, 'open', False)` 会恒为 False，
  于是「所有连接都被当成死的」→ 广播失聪。这个坑不再有第二次机会：
  本文件是唯一判活的地方，函数名叫 `_is_alive`，全仓 grep 得到。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Iterable

from websockets.protocol import State

from .config import CONFIG
from .contract import (
    NO_SEQ_EVENT_TYPES,
    STRUCTURAL_EVENT_TYPES,
    UNREAD_EVENT_TYPES,
    ContractViolation,
    now_ts,
)
from .ring import RingBuffer
from .privacy import sanitize_event

log = logging.getLogger("knowe.hub")


# ═══════════════════════════════════════════════════════════════
# 客户端
# ═══════════════════════════════════════════════════════════════

class Client:
    """一条 WebSocket 连接。每个客户端记住自己收过哪些 project_created。"""

    _next_id = 0

    def __init__(self, ws: Any) -> None:
        Client._next_id += 1
        self.id = f"c{Client._next_id}"
        self.ws = ws
        # ★ 每客户端独立的「已发过 project_created 的项目集合」——握手时按此补发
        self.sent_projects: set[str] = set()
        self.handshake_done = False

    @property
    def alive(self) -> bool:
        return _is_alive(self.ws)

    async def send(self, event: dict[str, Any]) -> bool:
        if not self.alive:
            return False
        try:
            await self.ws.send(json.dumps(event, ensure_ascii=False))
            return True
        except Exception as exc:  # 连接在发的过程中断了
            log.debug("send failed to %s: %s", self.id, exc)
            return False


def _is_alive(ws: Any) -> bool:
    """★ 唯一判活口径（BUG-1）：websockets≥14 用 State.OPEN，没有 .open 属性。"""
    return getattr(ws, "state", None) is State.OPEN


# ═══════════════════════════════════════════════════════════════
# 项目
# ═══════════════════════════════════════════════════════════════

class Project:
    def __init__(self, project_id: str, name: str, ring_capacity: int) -> None:
        self.id = project_id
        self.name = name
        self.seq = 0                       # 已盖出的最大 seq（0 = 还没有事件）
        self.ring = RingBuffer(ring_capacity)
        self.last_read_seq = 0             # 已读水位（客户端上报）
        self.members: list[dict[str, str]] = []
        self.pending_card: dict[str, Any] | None = None

    # unread_count 已上移到 Hub.unread_count()（v1.0.35.3）：未读须按「消息数」算，
    # 消息全集在 durable_conversation（Hub 层有 store 才能读盘），Project 只有 ring。


# ═══════════════════════════════════════════════════════════════
# Hub
# ═══════════════════════════════════════════════════════════════

class Hub:
    def __init__(self, store: Any | None = None) -> None:
        self.projects: dict[str, Project] = {}
        self.clients: set[Client] = set()
        self._seq_lock = asyncio.Lock()    # ★ 单点盖号锁
        # [v0.4] 落盘钩子（backend.persist.Store）。None = 纯内存，行为和 v0.3 一模一样。
        #   放在这里而不是放进 emit 的调用方，是因为 emit 是**唯一**的出站口——
        #   挂在这里，就不存在「某条事件忘了存」这回事。
        self.store = store
        # project_id → 用户可见文本过滤器。由 ProjectEngine 注册，Hub 是最终强制出口。
        self._public_text_filters: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        # project_id -> deterministic projection event_id -> committed envelope.
        # The cache is backed by the durable event log on first lookup so outbox replay
        # after a process restart cannot create a second user/UI message.
        self._idempotent_events: dict[str, dict[str, dict[str, Any]]] = {}

    @staticmethod
    def _idempotency_payload(event: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in event.items()
            if key not in {"seq", "ts", "project_id", "project_name"}
        }

    def _find_idempotent_event(
        self,
        project_id: str,
        event_id: str,
        proj: Project,
    ) -> dict[str, Any] | None:
        cache = self._idempotent_events.setdefault(project_id, {})
        if event_id in cache:
            return dict(cache[event_id])
        for candidate in reversed(proj.ring.events()):
            if candidate.get("event_id") == event_id:
                cache[event_id] = dict(candidate)
                return dict(candidate)
        if self.store is not None:
            try:
                durable = self.store.load_all_events(project_id)
            except Exception:
                durable = []
            for candidate in reversed(durable):
                if candidate.get("event_id") == event_id:
                    cache[event_id] = dict(candidate)
                    return dict(candidate)
        return None

    # ── 客户端 ──
    def add_client(self, client: Client) -> None:
        self.clients.add(client)

    def remove_client(self, client: Client) -> None:
        self.clients.discard(client)

    def prune(self) -> None:
        dead = {c for c in self.clients if not c.alive}
        for c in dead:
            self.clients.discard(c)
        if dead:
            log.debug("pruned %d dead clients", len(dead))

    @property
    def client_count(self) -> int:
        return len([c for c in self.clients if c.alive])

    # ── 用户可见文本守卫 ──
    def set_public_text_filter(
        self, project_id: str, fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """给项目登记最终出站过滤器。结构字段不动，只处理自然语言。"""
        self._public_text_filters[project_id] = fn

    def _filter_public_text(self, project_id: str, event: dict[str, Any]) -> dict[str, Any]:
        fn = self._public_text_filters.get(project_id)
        if fn is None:
            # 没有项目引擎（例如目录失效隔离期）也不能绕过硬脱敏。
            return sanitize_event(event, {})
        try:
            return fn(event)
        except Exception:
            # 注册过滤器若因花名册等外围状态出错，仍退回无状态硬过滤；绝不把原文直通。
            log.exception("[%s] 用户可见文本过滤失败，退回默认硬过滤", project_id)
            return sanitize_event(event, {})

    def clear_public_text_filter(self, project_id: str) -> None:
        """项目引擎关闭后解除 bound-method 引用，避免隔离期仍把整台旧引擎挂在内存里。"""
        self._public_text_filters.pop(project_id, None)

    # ── 项目 ──
    def get_or_create(self, project_id: str, name: str | None = None) -> Project:
        proj = self.projects.get(project_id)
        if proj is None:
            proj = Project(project_id, name or project_id, CONFIG.ring_capacity)
            self.projects[project_id] = proj
            log.info("project created: %s (%s)", project_id, proj.name)
        elif name and proj.name == proj.id:
            proj.name = name
        return proj

    # ── [v0.4] 温载：把磁盘上的历史灌回内存 ──
    def restore(
        self, project_id: str, name: str, events: list[dict[str, Any]],
        seq_watermark: int = 0,
    ) -> Project:
        """
        从落盘的事件流水重建一个项目：ring 灌满、seq 接上。

        seq 必须接着最大的那个往下走——接错了，前端会把新事件当成重复的丢掉，
        界面就再也不动了。

        ★ [v0.12 D · 问题二] **ring 的容量按「这次要温载多少历史」现算**，
          至少 CONFIG.ring_capacity，装不下就撑大到刚好装得下（再留一截余量）。

          为什么要这样：前端重连时靠 ring 的增量回放拿历史（replay_since(0)）。
          ring 要是比历史小，最老那批就被挤掉了 → replay 报 gap → 前端只能拿到
          「最近一屏」，用户一看「我前面的聊天呢？」。历史一条没丢（磁盘上全在），
          但没喂进 ring 就等于没展示。
          现在落盘的只剩结构事件（小），把整段历史灌进 ring 内存完全扛得住。
          （运行期新来的瞬时事件会往 ring 里加，maxlen 兜底不让它无限涨；
            真被挤掉的也只是逐字增量，无所谓——磁盘那份才是聊天记录的真相。）
        """
        capacity = max(CONFIG.ring_capacity, len(events) + 256)
        proj = Project(project_id, name, capacity)
        for ev in events:
            proj.ring.append(ev)
        proj.seq = max(max((int(e["seq"]) for e in events), default=0), int(seq_watermark or 0))
        # v1.0.35.3: 恢复已读水位（跨重启保留未读语义）。
        # 老数据无 .read 记录 → 历史算已读并立即落盘（last_read_seq = seq），
        # 避免升级后「全部历史变未读」吓到用户，同时让升级后的新消息能正确算未读。
        if self.store is not None:
            read = self.store.load_read_watermark(project_id)
            if read > 0:
                proj.last_read_seq = read
            else:
                proj.last_read_seq = proj.seq
                self.store.save_read_watermark(project_id, proj.seq)
        self.projects[project_id] = proj
        return proj

    def has(self, project_id: str) -> bool:
        return project_id in self.projects

    def remove_project(self, project_id: str) -> bool:
        """从实时 Hub 精确移除一个群或 DM，并清掉客户端宣告/过滤器引用。幂等。"""
        existed = self.projects.pop(project_id, None) is not None
        self._idempotent_events.pop(project_id, None)
        self.clear_public_text_filter(project_id)
        for client in self.clients:
            client.sent_projects.discard(project_id)
        return existed

    def remove_project_tree(self, project_id: str) -> int:
        """移除项目群及其全部 ``dm:{project}:*`` 实时频道。"""
        targets = [
            pid for pid in list(self.projects)
            if pid == project_id or pid.startswith(f"dm:{project_id}:")
        ]
        for pid in targets:
            self.remove_project(pid)
        return len(targets)

    def replay(
        self, project_id: str, since_seq: int,
    ) -> tuple[list[dict[str, Any]], bool, str]:
        """
        重连回放。JSONL 是持久真源，ring 只是实时增量层。

        · since_seq == 0：总是把磁盘全量结构历史与 ring 合并，保证 Electron 单独重开时
          初始消息/审批卡不会因运行期 ring 淘汰而消失。
        · ring 已有 gap：有落盘时用磁盘兜底，不把“磁盘完整、界面缺一块”推给前端。
        · 纯内存模式：仍保留原有 resync_required 语义。
        """
        proj = self.get_or_create(project_id)
        ring_events, gap = proj.ring.replay_since(since_seq)

        if since_seq > proj.seq:
            return [], True, "client-ahead"

        if self.store is not None and (since_seq == 0 or gap):
            durable = self.store.load_all_events(project_id)
            merged: dict[int, dict[str, Any]] = {}
            for ev in durable:
                seq = ev.get("seq")
                if isinstance(seq, int) and seq > since_seq:
                    merged[seq] = ev
            # 持久回放只重建结构状态。旧 stream_delta/tool_start 等瞬时帧若被重放，
            # 反而可能让刚重连的前端留下一个永远转圈的“正在输入”状态。
            # Ring 在这里仅补“已经形成结构结果、但磁盘写入偶发失败”的事件。
            for ev in proj.ring.structural(STRUCTURAL_EVENT_TYPES):
                seq = ev.get("seq")
                if isinstance(seq, int) and seq > since_seq:
                    merged[seq] = ev
            events = [self._filter_public_text(project_id, merged[k]) for k in sorted(merged)]
            return events, False, "disk+ring"

        events = [self._filter_public_text(project_id, ev) for ev in ring_events]
        return events, gap, "ring"

    def durable_conversation(self, project_id: str) -> list[dict[str, Any]]:
        """快照的 conversation：磁盘全量结构历史 + ring 中尚未落盘的结构事件。"""
        proj = self.get_or_create(project_id)
        merged: dict[int, dict[str, Any]] = {}
        if self.store is not None:
            for ev in self.store.load_all_events(project_id):
                seq = ev.get("seq")
                if isinstance(seq, int) and ev.get("type") in STRUCTURAL_EVENT_TYPES:
                    merged[seq] = ev
        for ev in proj.ring.structural(STRUCTURAL_EVENT_TYPES):
            seq = ev.get("seq")
            if isinstance(seq, int):
                merged[seq] = ev
        return [self._filter_public_text(project_id, merged[k]) for k in sorted(merged)]
    def durable_since(self, project_id: str, after_seq: int) -> list[dict[str, Any]]:
        """
        [v1.0.23.6] 增量读取：磁盘全量结构历史 + ring 未落盘结构事件，只留 seq > after_seq。

        与 durable_conversation 同源（同一合并逻辑），差异只在 seq 过滤——
        供 HTTP 旁路增量接口（GET /api/events）使用，前端启动预热「先有内容再等快照」。

        · 返回按 seq 升序、已脱敏（_filter_public_text）的结构事件；
        · after_seq 为 0 时与 durable_conversation 等价（全量）；
        · 空增量返回 []，前端据此判断「已同步到最新」。
        """
        if after_seq < 0:
            after_seq = 0
        proj = self.get_or_create(project_id)
        merged: dict[int, dict[str, Any]] = {}
        if self.store is not None:
            for ev in self.store.load_all_events(project_id):
                seq = ev.get("seq")
                if isinstance(seq, int) and seq > after_seq \
                        and ev.get("type") in STRUCTURAL_EVENT_TYPES:
                    merged[seq] = ev
        for ev in proj.ring.structural(STRUCTURAL_EVENT_TYPES):
            seq = ev.get("seq")
            if isinstance(seq, int) and seq > after_seq:
                merged[seq] = ev
        return [self._filter_public_text(project_id, merged[k]) for k in sorted(merged)]

    def durable_before(
        self, project_id: str, before_seq: int, limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """
        [启动时快速加载] 向前翻页：取 seq < before_seq 的**最近 limit 条**结构事件。

        与 durable_since 同源（同一合并逻辑），方向相反——durable_since 取「之后」
        （增量预热），这里取「之前」（上翻加载更早历史）。

        · 返回 (events, has_more)：events 按 seq 升序、已脱敏（_filter_public_text）；
          has_more = 还有比 events 首条更早的历史（前端据此决定是否继续显示加载标记）。
        · limit <= 0 → 视为 1（防呆；调用方应传正数）。
        · before_seq <= 0 / 无更早事件 → 返回 ([], False)。
        """
        if limit <= 0:
            limit = 1
        if before_seq <= 0:
            return [], False
        proj = self.get_or_create(project_id)
        merged: dict[int, dict[str, Any]] = {}
        if self.store is not None:
            for ev in self.store.load_all_events(project_id):
                seq = ev.get("seq")
                if isinstance(seq, int) and seq < before_seq \
                        and ev.get("type") in STRUCTURAL_EVENT_TYPES:
                    merged[seq] = ev
        for ev in proj.ring.structural(STRUCTURAL_EVENT_TYPES):
            seq = ev.get("seq")
            if isinstance(seq, int) and seq < before_seq:
                merged[seq] = ev
        ordered = [merged[k] for k in sorted(merged)]
        if not ordered:
            return [], False
        has_more = len(ordered) > limit
        tail = ordered[-limit:]
        return [self._filter_public_text(project_id, ev) for ev in tail], has_more

    # ── 发事件：引擎级（盖 seq、进 ring、广播） ──
    async def emit(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        引擎级事件的唯一出口：
          注入 project_id / project_name / ts → 锁内盖 seq → 进 ring → 校验 → 广播。
        """
        etype = payload.get("type")
        if etype in NO_SEQ_EVENT_TYPES:
            raise ContractViolation(f"{etype} 属无 seq 白名单，不能走 emit()（会被盖上 seq）")

        proj = self.get_or_create(project_id)

        event = dict(payload)
        event["project_id"] = project_id
        event["ts"] = now_ts()
        # user_echo 的契约里没有 project_name 字段（envelope.ts 逐字段核对过）——不能塞
        if etype != "user_echo":
            event["project_name"] = proj.name
        event = self._filter_public_text(project_id, event)

        async with self._seq_lock:          # ★ 单点盖号：跨项目也串行，杜绝竞态
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                existing = self._find_idempotent_event(project_id, event_id, proj)
                if existing is not None:
                    if self._idempotency_payload(existing) != self._idempotency_payload(event):
                        # Completion-triggered coordinator turns can be replayed after a
                        # process dies between user-message persistence and notification
                        # acknowledgement.  The first durable response is authoritative;
                        # a nondeterministic model rerun must not create a second user
                        # bubble or replace that response.
                        if event_id.startswith("coordmsg_"):
                            return existing
                        raise ContractViolation(
                            f"event_id {event_id!r} 已用于不同 payload，拒绝覆盖"
                        )
                    return existing
            proj.seq += 1
            event["seq"] = proj.seq
            proj.ring.append(event)
            if self.store is not None:
                # 结构事件落盘：[v1.0.24.4] 提交进持久化队列即返回，磁盘写入在后台
                # 单线程上跑。提交发生在 seq 锁内 → 入队顺序 = seq 顺序 → 盘上不乱序。
                _store, _pid, _event, _seq = self.store, project_id, event, proj.seq
                _store.defer_bg(lambda: _store.append_event(_pid, _event),
                                description=f"{_pid} append_event")
                _store.defer_bg(lambda: _store.save_seq_watermark(_pid, _seq),
                                description=f"{_pid} seq 高水位")
            if isinstance(event_id, str) and event_id:
                self._idempotent_events.setdefault(project_id, {})[event_id] = dict(event)

        await self.broadcast(event)
        return event

    # ── 发事件：服务器级（无 seq，旁路） ──
    async def emit_no_seq(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = dict(payload)
        pid = event.get("project_id")
        if isinstance(pid, str):
            event = self._filter_public_text(pid, event)
        await self.broadcast(event)
        return event

    async def send_to(self, client: Client, payload: dict[str, Any]) -> None:
        """点对点（握手回放、pong）。不进 ring、不广播。"""
        event = dict(payload)
        pid = event.get("project_id")
        if isinstance(pid, str):
            event = self._filter_public_text(pid, event)
        else:
            event = sanitize_event(event, {})
        await client.send(event)

    # ── 广播 ──
    async def broadcast(self, event: dict[str, Any], targets: Iterable[Client] | None = None) -> None:
        clients = list(targets if targets is not None else self.clients)
        if not clients:
            return
        results = await asyncio.gather(
            *(c.send(event) for c in clients), return_exceptions=True
        )
        for client, ok in zip(clients, results):
            if ok is not True:
                self.clients.discard(client)

        # project_created 广播后，记账到每个活着的客户端（避免握手时重复补发）
        if event.get("type") == "project_created":
            pid = event["project_id"]
            for c in clients:
                if c.alive:
                    c.sent_projects.add(pid)

    # ── 快照 ──
    async def snapshot(
        self, project_id: str,
        activity: list[dict[str, Any]] | None = None,
        *,
        limit: int = 0,
    ) -> dict[str, Any]:
        """
        state_snapshot：**本身消耗一个 seq 并写入 ring**（PROTOCOL.md §e）。
        conversation 只放结构事件（stream_delta 这类瞬时事件不进时间线）。

        [v1.0.24.4] activity = 引擎权威活动账本全量条目（见 engine.open_activity_snapshot）。
        传 None = 不带（调用方拿不到引擎）；传 [] = 明确告知「现场无人在干活」。

        [启动时快速加载] limit > 0 = 首屏裁剪：conversation 只下发最近 limit 条结构事件，
        附 total_count（全量条数）与 has_more（是否还有更早历史，前端据此翻页）。

        ★ 红线（分家铁律）：裁剪只发生在「下发/落盘的这个快照事件」上；
          磁盘 JSONL 持久历史（persist.load_all_events）与 compact 路径**零改动**——
          聊天记录权威仍在磁盘，快照只是某一时刻的投影基准，裁剪后前端用
          request_history 按需翻页取更早历史，不会丢数据。

        ★ unread 语义：未读数必须基于**全量** conversation 计算（裁剪前算），
          否则裁剪后 unread 会随截断虚低。
        """
        proj = self.get_or_create(project_id)

        async with self._seq_lock:
            # conversation 与 last_seq 必须在同一把 seq 锁下取，避免并发 emit 落在两者之间，
            # 造成“last_seq 已包含某条消息、快照正文却漏了它”的不一致快照。
            conversation = self.durable_conversation(project_id)
            total_count = len(conversation)
            unread_count = self._count_unread(proj.last_read_seq, conversation)
            last_seq = proj.seq
            proj.seq += 1

            trimmed = conversation
            if limit and limit > 0 and total_count > limit:
                trimmed = conversation[-limit:]
            has_more = total_count > len(trimmed)

            event = {
                "type": "state_snapshot",
                "project_id": project_id,
                "last_seq": last_seq,
                "agents": list(proj.members),
                "conversation": trimmed,
                "pending_card": proj.pending_card,
                "unread_count": unread_count,
                "total_count": total_count,
                "has_more": has_more,
                "ts": now_ts(),
                "seq": proj.seq,
            }
            if activity is not None:
                event["activity"] = list(activity)
            event = self._filter_public_text(project_id, event)
            proj.ring.append(event)
            if self.store is not None:
                # state_snapshot 本身不落聊天流水，但 seq 高水位必须持久化。
                # [v1.0.24.4] 同 emit：入队即返回，写盘在后台单线程（顺序由队列保证）。
                _store, _pid, _event, _seq = self.store, project_id, event, proj.seq
                _store.defer_bg(lambda: _store.append_event(_pid, _event),
                                description=f"{_pid} snapshot append_event")
                _store.defer_bg(lambda: _store.save_seq_watermark(_pid, _seq),
                                description=f"{_pid} snapshot seq 高水位")

        await self.broadcast(event)
        return event

    # ── 已读水位 ──
    def unread_count(self, project_id: str) -> int:
        """未读消息数 = 已读水位之后的消息/审批卡数（与前端 isUnreadEvent 对齐）。

        v1.0.35.3 之前用 seq 事件数（未读虚高，如 186 vs 实际 6 条），现改为数
        durable_conversation 里的 message / approval_card。读盘发生在握手/快照路径，
        非逐消息路径，可接受。
        """
        proj = self.projects.get(project_id)
        if proj is None:
            return 0
        return self._count_unread(proj.last_read_seq, self.durable_conversation(project_id))

    @staticmethod
    def _count_unread(last_read_seq: int, conversation: list[dict[str, Any]]) -> int:
        return sum(
            1 for ev in conversation
            if ev.get("seq", 0) > last_read_seq and ev.get("type") in UNREAD_EVENT_TYPES
        )

    def mark_read(self, project_id: str, seq: int) -> None:
        proj = self.projects.get(project_id)
        if proj is not None:
            proj.last_read_seq = max(proj.last_read_seq, seq)
            # v1.0.35.3: 落盘，否则重启归零、全部历史被算成未读。
            if self.store is not None:
                _pid, _seq = project_id, proj.last_read_seq
                self.store.defer_bg(lambda: self.store.save_read_watermark(_pid, _seq),
                                    description=f"{_pid} 已读水位")

    # ── /health ──
    def health(self) -> dict[str, Any]:
        self.prune()
        return {
            "status": "ok",
            "project_count": len(self.projects),
            "ws_clients": self.client_count,
        }

