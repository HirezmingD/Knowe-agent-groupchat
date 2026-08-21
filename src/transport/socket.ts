/**
 * socket.ts — Knowe WebSocket 传输层（v2 · Claude 审计版）
 *
 * 按 Claude 审计报告 §3.5（状态机）+ §3.6（哨兵）+ §2.3（时序契约）完整重写。
 *
 * 五大机制：
 *   ① 握手缓冲（§2.3-b） — replay_request→replay_complete 之间事件入缓冲
 *   ② 水位按项目（§2.3-a） — watermarks: Record<ProjectId, number>，单点持有
 *   ③ 纪元校准（§2.3-c） — last_seq < 水位 ⇒ 新纪元 ⇒ 清会话 + request_snapshot
 *   ④ 按项目去抖 resync（§2.3-d） — gap → request_snapshot(project_id)，800ms 去抖
 *   ⑤ 回声哨兵（§3.6） — 5s 无 echo ⇒ 转存疑 + 自动重连重同步
 *
 * 铁律：
 *   - seq 水位只在 transport 一处持有（不进 store）
 *   - 所有丢弃/拒收/哨兵触发必须出声（console.warn + 将来进走廊）
 *   - transport 不引用 store，只通过回调向外投递
 *
 * version: 2
 * changelog:
 *   v2.1 (2026-07-13) [v0.8b #1]:
 *     - state_snapshot 不再参与水位/空洞判定：它是**新基准**，直接落地并重置水位
 *       （原来：空洞触发的快照回来时 seq 又超前，被判成新空洞 → 卡死在 resync，
 *        修空洞的药被当成了新空洞）
 *     - state_snapshot 落地后把状态拨回 live（原来 resync 有去无回：只有
 *       replay_complete 会解除它，而快照不走那条路 → 界面永久「同步中」）
 *     - requestSnapshot 成为切群时的正路（store.switchProject 调它）
 *   v2 (2026-07-12): Claude 审计全面修正
 *     - 五大机制全部落地
 *     - 状态机五态（closed/connecting/handshaking/live/resync/reconnecting）
 *     - request_snapshot 带 project_id（§2.2#5）
 *     - approve/reject project_id 必填（§2.2#2）
 *     - user_message client_msg_id 必填（§2.2#1）
 *     - 无 seq 事件白名单旁路
 *     - ErrorEvent 兼容服务器级（无 project_id/seq）
 *   v1 (2026-07-11): 从 vanilla socket.js 移植
 */

/* eslint-disable no-console */
import type {
  InboundEvent,
  OutboundCommand,
  ProjectDirectoryRequired,
  ProjectDirectoryRestored,
  ProjectId,
  ForwardedPayload,
  ActivityLedgerEntry,
} from '../contract/envelope';
import { validateInbound, NO_SEQ_EVENT_TYPES } from '../contract/envelope';
import { runtimeWsUrl } from '../shared/runtimeEndpoints';
import i18n from '../i18n';

// ═══════════════════════════════════════════════════════════════
// [v1.0.18.4] WebSocket 认证：通过 URL query 参数传递 token
// Electron 的 onBeforeSendHeaders 无法可靠拦截 ws:// 升级请求，
// 因此改为从 window.knowe 异步获取 token 并拼入 URL。
// ═══════════════════════════════════════════════════════════════

let authTokenCache = '';
let authTokenReady = false;

async function ensureAuthToken(): Promise<void> {
  if (authTokenReady) return;
  try {
    const bridge = (window as any).knowe;
    if (bridge?.getRuntimeToken) {
      authTokenCache = (await bridge.getRuntimeToken()) || '';
    }
  } catch {
    // 浏览器环境或 preload 未就绪，使用空 token（后端不强制时放行）
  }
  authTokenReady = true;
}

function getAuthWsUrl(): string {
  const base = runtimeWsUrl();
  if (!authTokenCache) return base;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}token=${encodeURIComponent(authTokenCache)}`;
}

/** 初始化 WebSocket 认证令牌。在 createSocket + connect 之前调用。 */
export async function initWsAuthToken(): Promise<void> {
  await ensureAuthToken();
}

// ═══════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════

/** Connection status */
export type ConnStatus = 'connecting' | 'handshaking' | 'live' | 'resync' | 'reconnecting' | 'closed';

/** Callbacks: transport → store */
export interface SocketCallbacks {
  /** Inbound event (already validated + routed) */
  onEvent: (event: InboundEvent) => void;
  /** Connection status change */
  onStatus: (status: ConnStatus) => void;
  /** Echo sentinel: user_message sent (for optimistic rendering) */
  onSent?: (cmid: string, projectId: string, content: string) => void;
  /** Echo sentinel: corresponding user_echo received */
  onEchoOk?: (cmid: string) => void;
  /** Echo sentinel: no echo within 5s — broadcast deafness suspected */
  onEchoLost?: (cmid: string, projectId: string) => void;
  /** Epoch reset: server restarted, store must clear project session */
  onEpochReset?: (projectId: string) => void;
  /** Project discovered via replay_complete (store should auto-register session if not exists) */
  onProjectDiscovered?: (projectId: string, projectName?: string) => void;
  /** [v1.0.24.4] replay_complete 携带的权威活动账本 → store 以此校准花名册忙碌状态 */
  onActivityLedger?: (projectId: string, activity: ActivityLedgerEntry[]) => void;
  /** Get current active project id (for replay_request project context) */
  getActiveProjectId?: () => ProjectId | null;
  /** 系统级项目目录恢复请求；不进入聊天时间线。 */
  onProjectDirectoryRequired?: (event: ProjectDirectoryRequired) => void;
  /** 项目目录已恢复；用于关闭/作废仍在等待的目录选择流程。 */
  onProjectDirectoryRestored?: (event: ProjectDirectoryRestored) => void;
  /**
   * [v0.3-走廊] 诊断口：每一次丢弃/拒收/失败/告警都从这里出声。
   *
   * 旧版每个丢弃点只写 console.warn，注释挂着「将来进走廊」——那个将来没来过，
   * 于是走廊的四个计数器永远是 0。这条回调就是把它接上。
   * 可选：不接也不影响传输层任何行为。
   */
  onDiagnostic?: (d: {
    dir?: 'in' | 'out';
    type: string;
    projectId?: string;
    seq?: number;
    verdict: 'applied' | 'bypass' | 'buffered' | 'dup' | 'gap'
      | 'rejected' | 'sentinel' | 'epoch' | 'failed';
    summary?: string;
  }) => void;
}

/** Socket configuration */
export interface SocketConfig {
  url?: string;
  callbacks: SocketCallbacks;
}

/** Public API returned by createSocket */
export interface SocketAPI {
  connect: () => void;
  disconnect: () => void;
  sendMessage: (
    content: string, projectId: string, clientMsgId?: string, attachments?: unknown[],
    forwarded?: ForwardedPayload,
  ) => string;
  approve: (approvalId: string, projectId: string) => void;
  reject: (approvalId: string, projectId: string) => void;
  /**
   * [v0.26] 「我有新意见」——**改卡面，不落定**。
   *
   * 和 approve / reject 并列：都是控制面，直达 gate，不经消息队列。
   * approve/reject 把卡**落定**；这一条把卡上的 instruction **换一版**
   * （卡还在等，倒计时照走）。
   */
  feedbackInstruction: (approvalId: string, projectId: string, feedback: string) => void;
  /**
   * [v0.29 问题二] 让一个正在干活的成员放下手里的活。
   *
   * 和 approve / reject / feedbackInstruction 并列：**控制面**，直达引擎，
   * 不经聊天消息。用户按下这个按钮是**终局**，不是一条要项目经理去理解的提议。
   */
  stopWorker: (projectId: string, agentId: string) => void;
  createProject: (
    projectId: string, projectName: string, projectDir?: string, approvalId?: string,
  ) => void;
  /** [v1.0.23.4] 群聊中途添加 Agent 员工（roles 为职能前缀数组，可重复）。 */
  addAgents: (projectId: string, roles: string[]) => void;
  /** [v1.0.38.2] 成员改名 / 换头像（跨项目全局生效）。name/avatar 至少传一个，空串=还原。 */
  updateAgentProfile: (projectId: string, agentId: string, attrs: { name?: string; avatar?: string }) => void;
  setProjectDirectory: (projectId: string, directory: string, requestId: string, projectName?: string) => void;
  cancelProjectDirectory: (projectId: string, requestId?: string) => void;
  requestSnapshot: (projectId: string, limit?: number) => void;
  /** [v1.0.39] 向前翻页：请求比 beforeSeq 更早的历史（上翻加载）。 */
  requestHistory: (projectId: string, beforeSeq: number, limit?: number) => void;
  /** [v0.48 token] 请求当前项目的 Token 消耗统计；可选以兼容旧测试桩/第三方 SocketAPI 实现。 */
  requestTokenUsage?: (projectId: string, requestId?: number) => void;
  /** [v0.48] 发送任意控制帧（token_usage_req 等旁路统计请求）。 */
  sendCommand: (frame: Record<string, unknown>) => void;
  /**
   * [v1.0.23.6] 增量注入后抬升水位：HTTP 旁路增量（/api/events）已把该项目的
   * seq ≤ lastSeq 的事件应用进 store——通知 socket 水位对齐，防止 WS replay
   * 把同批旧事件再投一遍（水位是幂等防线的第一道闸）。
   */
  noteIncremental: (projectId: string, lastSeq: number) => void;
  /** Per-project seq watermarks (read-only snapshot, for tests/debug) */
  readonly watermarks: Record<string, number>;
  /** Current connection status */
  readonly status: ConnStatus;
  /** Debug: raw WebSocket readyState */
  _debugReadyState: () => number | string;
  /** Test: inspect handshake buffer */
  _getHandshakeBuffer: () => unknown[];
  /** Test: inspect pending echoes */
  _getPendingEchoes: () => Record<string, { deadline: number; projectId: string }>;
}

// ═══════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════

const HANDSHAKE_BUFFER_MAX = 500;
const RESYNC_DEBOUNCE_MS = 800;
const ECHO_TIMEOUT_MS = 5000;
const ECHO_CHECK_INTERVAL_MS = 1000;
const RECONNECT_BACKOFF_INITIAL_MS = 500;
const RECONNECT_BACKOFF_MAX_MS = 8000;
const HEARTBEAT_INTERVAL_MS = 15000;
const PONG_TIMEOUT_MS = 8000;
const DEFAULT_PROJECT = 'demo';

// ═══════════════════════════════════════════════════════════════
// UUID helpers (minimal, no dependency)
// ═══════════════════════════════════════════════════════════════

function generateCmid(): string {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let result = 'cm_';
  for (let i = 0; i < 12; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

// ═══════════════════════════════════════════════════════════════
// createSocket
// ═══════════════════════════════════════════════════════════════

/*
 * [v1.0.23.2] 跨实例/跨连接事件去重（多连接重复广播根治）。
 *
 * 背景：Electron 页面曾出现 5 个 WS 连接并存（旧连接未及时关闭 / App 重挂载
 * 重建 socket），每个连接都收到后端广播 → 同一条消息被 applyEvent 5 次 →
 * 用户看到 5 条完全相同的回复气泡。watermark 是实例级闭包，挡不住跨实例重复。
 *
 * 这里用模块级 Set 兜底：同一项目同一 seq 同一 type 的事件全应用只投递一次。
 * seq 单调递增且唯一，天然是去重键（event_id 不是所有事件都有，seq 更普适）。
 * 容量封顶 4000，超出丢一半最旧，防无限增长。
 */
const _globalSeen = new Set<string>();
const _GLOBAL_SEEN_MAX = 4000;

function _markGlobalSeen(ev: { project_id?: unknown; seq?: unknown; type?: unknown }): boolean {
  const seq = ev.seq;
  if (typeof seq !== 'number') return false; // 无 seq 事件（NO_SEQ 旁路）不参与
  const key = `${ev.project_id ?? ''}:${seq}:${ev.type}`;
  if (_globalSeen.has(key)) return true;
  _globalSeen.add(key);
  if (_globalSeen.size > _GLOBAL_SEEN_MAX) {
    _globalSeen.clear(); // 防无限增长；清空后短期由实例 watermark 兜底
  }
  return false;
}

/** [v1.0.23.2] 测试专用：清空全局去重集合（vitest 各用例共享同一模块实例，seq 会复用）。 */
export function _resetGlobalSeen(): void {
  _globalSeen.clear();
}

export function createSocket({ url = runtimeWsUrl(), callbacks }: SocketConfig): SocketAPI {
  const {
    onEvent, onStatus, onSent, onEchoOk, onEchoLost, onEpochReset,
    onProjectDiscovered, getActiveProjectId,
    onProjectDirectoryRequired, onProjectDirectoryRestored,
    onActivityLedger,
  } = callbacks;
  // [v0.3-走廊] 没接诊断口时退化为空函数，行为完全不变
  const diag = callbacks.onDiagnostic ?? (() => { /* no-op */ });

  // ── WebSocket state ──
  let ws: WebSocket | null = null;

  // ── ② 水位按项目（§2.3-a）— 单点持有，不进 store ──
  const _watermarks: Record<string, number> = {};

  function getWatermark(pid: string): number {
    return _watermarks[pid] ?? 0;
  }
  function setWatermark(pid: string, n: number): void {
    _watermarks[pid] = n;
  }

  // ── Connection state ──
  let _status: ConnStatus = 'closed';
  let backoff = RECONNECT_BACKOFF_INITIAL_MS;

  function setStatus(s: ConnStatus): void {
    if (_status !== s) {
      _status = s;
      onStatus(s);
    }
  }

  // ── ① 握手缓冲（§2.3-b） ──
  let handshakeBuffer: Array<{ seq: number; event: InboundEvent }> = [];
  let handshakeActive = false;
  let handshakeProjectId: string | null = null;

  function pushHandshake(event: InboundEvent, seq: number, pid: string): void {
    if (handshakeBuffer.length >= HANDSHAKE_BUFFER_MAX) {
      console.warn('[socket] handshake buffer overflow (%d) — requesting snapshot for %s', HANDSHAKE_BUFFER_MAX, pid);
      handshakeBuffer = [];
      doRequestSnapshot(pid);
      return;
    }
    handshakeBuffer.push({ seq, event });
  }

  function drainHandshake(): void {
    handshakeActive = false;
    handshakeProjectId = null;
    if (handshakeBuffer.length === 0) return;

    // Sort by seq
    handshakeBuffer.sort((a, b) => a.seq - b.seq);

    // Dedup by seq (keep last occurrence — Map overwrites on duplicate key)
    const seen = new Map<number, InboundEvent>();
    for (const item of handshakeBuffer) {
      seen.set(item.seq, item.event); // last write wins
    }
    const deduped: Array<{ seq: number; event: InboundEvent }> = [];
    for (const [seq, event] of seen) {
      deduped.push({ seq, event });
    }

    console.log('[socket] draining handshake buffer: %d events → %d after dedup',
      handshakeBuffer.length, deduped.length);

    for (const { seq, event } of deduped) {
      const projId = (event as Record<string, unknown>).project_id as string
        || DEFAULT_PROJECT;
      const curSeq = getWatermark(projId);
      if (seq <= curSeq) {
        console.warn('[socket] handshake-drain drop dup project=%s seq=%d curSeq=%d type=%s',
          projId, seq, curSeq, event.type);
        continue;
      }
      // NOTE: No gap detection during handshake drain.
      // The handshake buffer naturally contains sparse seq ranges
      // (real-time events intercepted before replay history arrived).
      // Gaps are expected here — replay_complete.last_seq validates consistency.

      setWatermark(projId, seq);
      // [v1.0.23.2] 全局去重：跨实例/跨连接同 seq 事件只投递一次（多连接广播重复根治）
      if (_markGlobalSeen(event)) {
        console.warn('[socket] global dup drop project=%s seq=%d type=%s', projId, seq, event.type);
        continue;
      }
      onEvent(event);
    }

    handshakeBuffer = [];
  }

  // ── ④ 按项目去抖 resync（§2.3-d） ──
  const _resyncDebounce: Record<string, number> = {};

  function doRequestSnapshot(projectId: string, limit?: number): void {
    const now = Date.now();
    const last = _resyncDebounce[projectId] ?? 0;
    if (now - last < RESYNC_DEBOUNCE_MS) {
      console.log('[socket] resync debounced for %s (last=%dms ago)', projectId, now - last);
      return;
    }
    _resyncDebounce[projectId] = now;
    setStatus('resync');
    // [v1.0.39] 首屏裁剪：limit > 0 只下发最近 N 条（旧后端忽略未知字段 → 全量）
    const frame: Record<string, unknown> = { type: 'request_snapshot', project_id: projectId };
    if (limit !== undefined && Number.isInteger(limit) && limit > 0) frame.limit = limit;
    sendRaw(frame as OutboundCommand);
    console.log('[socket] request_snapshot sent for %s limit=%s', projectId, limit ?? 'full');
  }

  // ── ⑤ 回声哨兵（§3.6） ──
  interface PendingEcho {
    deadline: number;
    projectId: string;
    content: string;
  }
  const _pendingEchoes: Record<string, PendingEcho> = {};
  let echoCheckTimer: ReturnType<typeof setInterval> | null = null;
  let _echoReconnectDebounce = 0;

  function startEchoCheck(): void {
    if (echoCheckTimer) return;
    echoCheckTimer = setInterval(() => {
      const now = Date.now();
      for (const [cmid, pe] of Object.entries(_pendingEchoes)) {
        if (now >= pe.deadline) {
          console.warn('[socket] echo sentinel: timeout for cmid=%s project=%s — suspect broadcast deafness',
            cmid, pe.projectId);
          delete _pendingEchoes[cmid];
          if (onEchoLost) onEchoLost(cmid, pe.projectId);

          // Auto-reconnect (debounced: only once per 10s)
          if (now - _echoReconnectDebounce > 10000) {
            _echoReconnectDebounce = now;
            console.warn('[socket] echo sentinel: triggering auto-reconnect');
            reconnect();
          }
        }
      }
    }, ECHO_CHECK_INTERVAL_MS);
  }

  function stopEchoCheck(): void {
    if (echoCheckTimer) {
      clearInterval(echoCheckTimer);
      echoCheckTimer = null;
    }
  }

  function registerEcho(cmid: string, projectId: string, content: string): void {
    _pendingEchoes[cmid] = {
      deadline: Date.now() + ECHO_TIMEOUT_MS,
      projectId,
      content,
    };
    if (onSent) onSent(cmid, projectId, content);
  }

  function confirmEcho(cmid: string): void {
    if (_pendingEchoes[cmid]) {
      delete _pendingEchoes[cmid];
      if (onEchoOk) onEchoOk(cmid);
    }
  }

  // ── Heartbeat (§3.5) ──
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let pongDeadline: number | null = null;
  let hbSocket: WebSocket | null = null;

  function startHeartbeat(): void {
    stopHeartbeat();
    hbSocket = ws;
    pingTimer = setInterval(() => {
      if (hbSocket !== ws) return; // 旧定时器自动作废
      if (!ws || ws.readyState !== 1) return;
      ws.send(JSON.stringify({ type: 'ping' }));
      const myDeadline = Date.now() + PONG_TIMEOUT_MS;
      pongDeadline = myDeadline;
      setTimeout(() => {
        if (hbSocket === ws && pongDeadline === myDeadline && Date.now() >= myDeadline) {
          console.warn('[socket] pong timeout on live socket — closing to reconnect');
          try { ws?.close(); } catch { /* ignore */ }
        }
      }, PONG_TIMEOUT_MS);
    }, HEARTBEAT_INTERVAL_MS);
  }

  function stopHeartbeat(): void {
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
    hbSocket = null;
  }

  // ── Internal: send raw command ──
  function sendRaw(obj: OutboundCommand): boolean {
    const rdy = ws ? ws.readyState : null;
    if (ws && rdy === 1) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    console.warn('[socket] outbound dropped (ws=%s readyState=%s) type=%s',
      ws ? 'open' : 'null', rdy, obj.type);
    diag({ dir: 'out', type: obj.type, verdict: 'failed',
      summary: `出站失败：连接未就绪（readyState=${rdy}）` });   // [v0.3-走廊]
    return false;
  }

  // ── Internal: event routing with seq watermarks ──
  function routeEvent(rawObj: Record<string, unknown>): void {
    const rawType = rawObj.type as string | undefined;

    // ── 无 seq 白名单旁路 ──
    if (rawType && NO_SEQ_EVENT_TYPES.has(rawType)) {
      handleServerControlEvent(rawObj, rawType);
      return;
    }

    // ── Server-level error: no project_id/seq ──
    if (rawType === 'error' && rawObj.seq === undefined && rawObj.project_id === undefined) {
      const ev = validateInbound(rawObj);
      if (ev) {
        onEvent(ev);
        console.warn('[socket] server-level error (no project_id/seq): %s', rawObj.message);
      } else {
        console.error('[socket] server-level error zod REJECTED', JSON.stringify(rawObj));
        diag({ type: 'error', verdict: 'rejected', summary: i18n.t('socket.01') }); // [v0.3-走廊]
      }
      return;
    }

    // ── Normal events: Zod validation ──
    const ev = validateInbound(rawObj);
    if (!ev) {
      console.error('[socket] zod REJECTED type=%s', rawType, JSON.stringify(rawObj));
      diag({                                                        // [v0.3-走廊]
        type: rawType ?? 'unknown',
        projectId: rawObj.project_id as string | undefined,
        seq: rawObj.seq as number | undefined,
        verdict: 'rejected',
        summary: i18n.t('socket.04'),
      });
      return;
    }

    const projId = (rawObj.project_id as string) || DEFAULT_PROJECT;
    const evSeq = (rawObj.seq as number) ?? null;

    // ── Handshake buffering ──
    if (handshakeActive && evSeq !== null) {
      diag({ type: ev.type, projectId: projId, seq: evSeq, verdict: 'buffered',   // [v0.3-走廊]
        summary: i18n.t('socket.06') });
      pushHandshake(ev, evSeq, projId);
      return;
    }

    /*
     * [v0.8b #1] ★ state_snapshot 走**独木桥**：不比水位，不判空洞，直接落地。
     *
     *   快照不是一条增量，它是**新的基准**——「这个项目现在长这样」。
     *   拿它去跟旧水位比对，是拿地基去对齐砖缝。
     *
     *   不这么做会出两件事，都很难看：
     *
     *   ① **空洞永远修不好。** 水位 5、事件 6~9 丢了、第 10 条到 → 判定空洞 →
     *      请求快照 → 快照带着 seq=11 回来 → 11 > 5+1 → **又是空洞** →
     *      再请求（800ms 去抖，多半直接被吃掉）→ 卡死在 resync。
     *      修空洞的那剂药，被判定成了新的空洞。
     *
     *   ② **界面永久「同步中」。** doRequestSnapshot 把状态置成 'resync'，
     *      而只有 replay_complete 会把它拨回 'live'——快照不走那条路
     *      （后端 _cmd_request_snapshot 只回一条 state_snapshot）。
     *
     *   这两个洞一直都在，只是 v0.8b 之前没人主动请求过快照，所以没踩到。
     *   现在切群每次都要请求，它们就成了拦路虎。
     */
    if (ev.type === 'state_snapshot') {
      const snapSeq = Math.max(evSeq ?? 0, (rawObj.last_seq as number) ?? 0);
      setWatermark(projId, snapSeq);              // 快照就是新的地基
      if (_status === 'resync') setStatus('live'); // 同步完了，别再挂着「同步中」
      diag({ type: ev.type, projectId: projId, seq: snapSeq, verdict: 'applied',
        summary: i18n.t('socket.05') });
      onEvent(ev);
      return;
    }

    /*
     * [v1.0.39] ★ history_events 也走独木桥：旁路响应（无 seq、不进水位/去重/空洞判定）。
     *   它是「向前翻页」的结果——把更早的历史补进会话头部。不比水位、不判空洞，
     *   直接投递给 store（由 history 注入逻辑按 seq 归位到 items 头部）。
     */
    if (ev.type === 'history_events') {
      diag({ type: ev.type, projectId: projId, verdict: 'applied',
        summary: 'history_events 旁路注入' });
      onEvent(ev);
      return;
    }

    // ── Seq watermark + gap detection ──
    if (evSeq !== null && evSeq !== undefined) {
      const curSeq = getWatermark(projId);
      if (evSeq <= curSeq) {
        console.warn('[socket] drop dup project=%s seq=%d curSeq=%d type=%s', projId, evSeq, curSeq, ev.type);
        diag({ type: ev.type, projectId: projId, seq: evSeq, verdict: 'dup',  // [v0.3-走廊]
          summary: `重复丢弃（水位 ${curSeq}）` });
        return;
      }
      if (curSeq > 0 && evSeq > curSeq + 1) {
        console.warn('[socket] gap detected project=%s seq=%d curSeq=%d type=%s — requesting resync',
          projId, evSeq, curSeq, ev.type);
        diag({ type: ev.type, projectId: projId, seq: evSeq, verdict: 'gap',  // [v0.3-走廊]
          summary: `seq 空洞（水位 ${curSeq}）→ 请求快照` });
        doRequestSnapshot(projId);
        return;
      }
      setWatermark(projId, evSeq);
    }

    diag({ type: ev.type, projectId: projId, seq: evSeq ?? undefined, verdict: 'applied' }); // [v0.3-走廊]
    // [v1.0.23.2] 全局去重：跨实例/跨连接同 seq 事件只投递一次（多连接广播重复根治）
    if (_markGlobalSeen(ev)) {
      console.warn('[socket] global dup drop project=%s seq=%d type=%s', projId, evSeq, ev.type);
      return;
    }
    onEvent(ev);
  }

  function handleServerControlEvent(rawObj: Record<string, unknown>, rawType: string): void {
    switch (rawType) {
      case 'replay_complete': {
        const rcEv = rawObj as { last_seq: number; project_id?: string; note?: string };
        const pid = rcEv.project_id || DEFAULT_PROJECT;
        const lastSeq = rcEv.last_seq;

        // ③ 纪元校准（§2.3-c）
        const curWatermark = getWatermark(pid);
        if (lastSeq < curWatermark) {
          console.warn('[socket] epoch reset: last_seq=%d < watermark=%d for %s — clearing session',
            lastSeq, curWatermark, pid);
          diag({ type: 'replay_complete', projectId: pid, seq: lastSeq, verdict: 'epoch',  // [v0.3-走廊]
            summary: `检测到服务重启（last_seq=${lastSeq} < 水位=${curWatermark}）→ 清会话重建` });
          if (onEpochReset) onEpochReset(pid);
          handshakeActive = false;
          handshakeBuffer = [];
          handshakeProjectId = null;
          setWatermark(pid, lastSeq);
          doRequestSnapshot(pid);
          setStatus('live');
          return;
        }

        // Drain handshake buffer BEFORE setting watermark
        // (so buffered events are compared against the old watermark)
        if (handshakeActive) {
          drainHandshake();
        }

        // Normal: update watermark
        setWatermark(pid, lastSeq);
        setStatus('live');
        // [v1.0.24.4] 账本转交：只在正常支转（纪元校准支随后必有快照自带账本）。
        // 旧后端不带 activity 字段 → 不触发 → 完全退回现状行为。
        const rcActivity = (rawObj as { activity?: unknown }).activity;
        if (onActivityLedger && Array.isArray(rcActivity)) {
          onActivityLedger(pid, rcActivity as ActivityLedgerEntry[]);
        }
        // Auto-register project if store doesn't have it yet
        if (onProjectDiscovered) onProjectDiscovered(pid, undefined);
        return;
      }

      case 'resync_required': {
        console.warn('[socket] resync_required — requesting snapshot');
        diag({ type: 'resync_required', verdict: 'gap',            // [v0.3-走廊]
          summary: i18n.t('socket.02') });
        const pid = handshakeProjectId || getActiveProjectId?.() || DEFAULT_PROJECT;
        doRequestSnapshot(pid);
        break;
      }

      case 'pong': {
        pongDeadline = null;
        return;
      }

      case 'project_created': {
        // ``request_project_id`` is a backward-compatibility correlation extension for old
        // p_ap_*/slug clients. Validate only the established contract shape, then reattach the
        // extension. Passing the extension into a strict Zod object rejects the entire event —
        // which was why v0.17 could leave the optimistic alias visible forever.
        const requestProjectId = rawObj.request_project_id;
        const contractObj = { ...rawObj };
        delete contractObj.request_project_id;
        const ev = validateInbound(contractObj);
        if (ev) {
          if (typeof requestProjectId === 'string' && requestProjectId) {
            onEvent(Object.assign({}, ev, { request_project_id: requestProjectId }) as InboundEvent);
          } else {
            onEvent(ev);
          }
        }
        return;
      }

      case 'project_directory_required': {
        const ev = validateInbound(rawObj);
        if (!ev || ev.type !== 'project_directory_required') {
          console.error('[socket] project_directory_required zod REJECTED', JSON.stringify(rawObj));
          diag({ type: rawType, verdict: 'rejected', summary: i18n.t('socket.07') });
          return;
        }
        diag({ type: rawType, projectId: ev.project_id, verdict: 'bypass',
          summary: i18n.t('socket.09') });
        if (onProjectDirectoryRequired) {
          onProjectDirectoryRequired(ev);
        } else {
          console.warn('[socket] project_directory_required received without a UI handler');
          diag({ type: rawType, projectId: ev.project_id, verdict: 'failed',
            summary: i18n.t('socket.03') });
        }
        return;
      }

      case 'project_directory_restored': {
        const ev = validateInbound(rawObj);
        if (!ev || ev.type !== 'project_directory_restored') {
          console.error('[socket] project_directory_restored zod REJECTED', JSON.stringify(rawObj));
          diag({ type: rawType, verdict: 'rejected', summary: i18n.t('socket.08') });
          return;
        }
        diag({ type: rawType, projectId: ev.project_id, verdict: 'bypass',
          summary: i18n.t('socket.10') });
        onProjectDirectoryRestored?.(ev);
        return;
      }

      // [v1.0.20.1] Token 统计响应是旁路控制帧（无 seq，不走 Zod 信封校验）：
      // 原样转发给 App 统一入口，由 handleTokenUsageEvent 消费（见 tokenUsage store）。
      case 'token_usage_res': {
        onEvent(rawObj as InboundEvent);
        return;
      }

      default:
        break;
    }
  }

  // ── Connection lifecycle ──

  function connect(): void {
    setStatus('connecting');

    // Close stale connection
    if (ws) {
      try { ws.close(1000, 'reconnecting'); } catch { /* ignore */ }
      ws = null;
    }

    // [v1.0.18.4] URL 带 token query 参数，绕过 onBeforeSendHeaders 的 ws:// 不匹配问题
    const tokenUrl = getAuthWsUrl();
    ws = new WebSocket(tokenUrl);
    const thisSocket = ws;

    ws.onopen = () => {
      // [v1.0.23.2] 活跃检查：旧连接（已被后续 connect 取代）的 onopen 迟到时，
      //   不能让它把 replay_request 发到新连接上（否则同一连接握手多次、重放多遍）。
      if (ws !== thisSocket) {
        console.warn('[socket] stale onopen ignored (superseded by newer connect)');
        return;
      }
      backoff = RECONNECT_BACKOFF_INITIAL_MS;
      const sock = thisSocket;
      if (!sock) return;

      // Enter handshaking
      handshakeActive = true;
      handshakeBuffer = [];
      handshakeProjectId = getActiveProjectId?.() ?? DEFAULT_PROJECT;
      setStatus('handshaking');

      // Send first frame: replay_request (must be within 5s)
      const activePid = getActiveProjectId?.() ?? DEFAULT_PROJECT;
      const sinceSeq = getWatermark(activePid);
      sock.send(JSON.stringify({
        type: 'replay_request',
        project_id: activePid,
        since_seq: sinceSeq,
      }));

      startHeartbeat();
      startEchoCheck();
      console.log('[socket] connected → handshaking, replay_request project=%s since_seq=%d',
        activePid, sinceSeq);
    };

    ws.onmessage = (e: MessageEvent) => {
      // [v1.0.23.2] 活跃检查：被取代的旧连接残留帧直接丢弃。
      //   多连接并存时（历史 bug：5 个 WS 连接同时收广播）这是第一道闸。
      if (ws !== thisSocket) return;
      let raw: unknown;
      try {
        raw = JSON.parse(e.data as string);
      } catch {
        console.warn('[socket] bad JSON:', (e.data as string)?.substring(0, 80));
        return;
      }

      // user_echo: special handling for echo sentinel
      if (typeof raw === 'object' && raw !== null) {
        const rawObj = raw as Record<string, unknown>;

        if (rawObj.type === 'user_echo') {
          const echoCmid = rawObj.client_msg_id as string | null | undefined;
          if (echoCmid) confirmEcho(echoCmid);
          // Forward to normal routing (for store's user_echo handling)
          routeEvent(rawObj);
          return;
        }
      }

      routeEvent(raw as Record<string, unknown>);
    };

    ws.onclose = (event: CloseEvent) => {
      // 4001: superseded by newer connection — yield, don't reconnect
      if (event?.code === 4001) {
        console.warn('[socket] superseded by a newer connection (code 4001) — yielding, will NOT reconnect');
        stopHeartbeat();
        stopEchoCheck();
        setStatus('closed');
        return;
      }

      // Only handle close if this is still the active socket
      if (ws !== thisSocket) return;

      stopHeartbeat();
      // Don't stop echo check — pending echoes should still report on reconnect
      scheduleReconnect();
    };

    ws.onerror = () => {
      try { ws?.close(); } catch { /* best effort */ }
    };
  }

  function reconnect(): void {
    // Close current connection
    if (ws) {
      try { ws.close(); } catch { /* ignore */ }
      ws = null;
    }
    stopHeartbeat();
    scheduleReconnect();
  }

  function scheduleReconnect(): void {
    setStatus('reconnecting');
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, RECONNECT_BACKOFF_MAX_MS);
  }

  // ═══════════════════════════════════════════════════════════════
  // Public API
  // ═══════════════════════════════════════════════════════════════

  return {
    connect,

    disconnect(): void {
      stopHeartbeat();
      stopEchoCheck();
      handshakeActive = false;
      handshakeBuffer = [];
      handshakeProjectId = null;
      if (ws) { ws.close(); }
      setStatus('closed');
    },

    sendMessage(
      content: string, projectId: string, clientMsgId?: string, attachments?: unknown[],
      forwarded?: ForwardedPayload,
    ): string {
      const cmid = clientMsgId || generateCmid();
      const pid = projectId || DEFAULT_PROJECT;

      // ⑤ Register echo sentinel
      registerEcho(cmid, pid, content);

      // Send (client_msg_id now required by v2 contract)
      // [v1.0.19.4] 附件（路径+签名，无字节）随消息出站；无附件时不带该字段，行为不变。
      // [v1.0.23.1] 转发结构化载荷（content = 用户配言原文；模板由后端构造）。
      const frame: Record<string, unknown> = {
        type: 'user_message',
        project_id: pid,
        content,
        client_msg_id: cmid,
      };
      if (attachments && attachments.length) frame.attachments = attachments;
      if (forwarded) frame.forwarded = forwarded;
      sendRaw(frame as OutboundCommand);

      return cmid;
    },

    approve(approvalId: string, projectId: string): void {
      sendRaw({
        type: 'approve',
        approval_id: approvalId,
        project_id: projectId,  // [v2#7] 必填
      });
    },

    reject(approvalId: string, projectId: string): void {
      sendRaw({
        type: 'reject',
        approval_id: approvalId,
        project_id: projectId,  // [v2#7] 必填
      });
    },

    feedbackInstruction(approvalId: string, projectId: string, feedback: string): void {
      sendRaw({
        type: 'feedback_instruction',
        approval_id: approvalId,
        project_id: projectId,
        feedback,
      });
    },

    stopWorker(projectId: string, agentId: string): void {
      sendRaw({
        type: 'stop_worker',
        project_id: projectId,
        agent_id: agentId,
      });
    },

    createProject(
      projectId: string, projectName: string, projectDir?: string, approvalId?: string,
      roles?: string[],
    ): void {
      const payload: Record<string, unknown> = {
        type: 'create_project', project_id: projectId, project_name: projectName,
      };
      if (projectDir) payload.project_dir = projectDir;
      // v0.18 optional extension: binds an approval-card creation to this canonical id.
      // Old clients omit it; old p_ap_* ids remain supported by the backend resolver.
      if (approvalId) payload.approval_id = approvalId;
      // [主动拉入worker] 建群时勾选的职能前缀；缺省/空 = 不选（后端兼容）。
      if (roles && roles.length) payload.roles = roles;
      sendRaw(payload as OutboundCommand);
    },

    /**
     * [v1.0.23.4] 群聊中途添加 Agent 员工：roles 为职能前缀数组，允许重复
     * （同职能多选），后端自动编号 fe_1 占用 → fe_2/fe_3…。
     */
    addAgents(projectId: string, roles: string[]): void {
      sendRaw({ type: 'add_agents', project_id: projectId, roles } as OutboundCommand);
    },

    /**
     * [v1.0.38] 成员改名 / 换头像（按项目隔离生效）。
     * name/avatar 至少传一个；空串 = 还原（回默认）。
     */
    updateAgentProfile(projectId: string, agentId: string, attrs: { name?: string; avatar?: string }): void {
      const payload: Record<string, unknown> = {
        type: 'update_agent_profile',
        project_id: projectId,
        agent_id: agentId,
      };
      if (typeof attrs.name === 'string') payload.name = attrs.name;
      if (typeof attrs.avatar === 'string') payload.avatar = attrs.avatar;
      sendRaw(payload as OutboundCommand);
    },

    setProjectDirectory(projectId: string, directory: string, requestId: string, projectName?: string): void {
      const payload: Record<string, unknown> = {
        type: 'set_project_directory',
        project_id: projectId,
        project_dir: directory,
        request_id: requestId,
      };
      // [v0.13 卡片] 恢复卡顺手改了名才带 project_name；没改就不带，后端按同名跳过改名。
      const nm = projectName?.trim();
      if (nm) payload.project_name = nm;
      sendRaw(payload as OutboundCommand);
    },

    cancelProjectDirectory(projectId: string, requestId?: string): void {
      sendRaw({
        type: 'cancel_project_directory',
        project_id: projectId,
        ...(requestId ? { request_id: requestId } : {}),
      });
    },

    /**
     * [v0.8b #1] 主动把某个项目的历史要过来（切群时 store 会调它）。
     *
     * 800ms 去抖是共用的：刚要过就再要一次，第二次会被吃掉——
     * 那本来也是白要（快照还在路上）。
     */
    requestSnapshot(projectId: string, limit?: number): void {
      doRequestSnapshot(projectId, limit);
    },

    /**
     * [v1.0.39] 向前翻页：请求比 beforeSeq 更早的历史（上翻加载）。
     * 独立于快照：history_events 是旁路响应（无 seq），不进水位/去重。
     */
    requestHistory(projectId: string, beforeSeq: number, limit?: number): void {
      if (!projectId || !Number.isInteger(beforeSeq) || beforeSeq <= 0) return;
      const payload: Record<string, unknown> = {
        type: 'request_history',
        project_id: projectId,
        before_seq: beforeSeq,
      };
      if (limit !== undefined && Number.isInteger(limit) && limit > 0) payload.limit = limit;
      sendRaw(payload as OutboundCommand);
    },

    /**
     * [v0.48 token] Token 统计是控制面旁路请求，不进入聊天消息队列，也不注册回声哨兵。
     * 和 approve / reject / requestSnapshot 等发送型方法一致，统一复用 sendRaw：
     * WebSocket 未就绪时由 sendRaw 记录诊断并安全丢弃，不改变连接状态机。
     */
    requestTokenUsage(projectId: string, requestId?: number): void {
      const payload: Record<string, unknown> = {
        type: 'token_usage_req',
        project_id: projectId,
      };
      if (requestId !== undefined) payload.request_id = requestId;
      sendRaw(payload as OutboundCommand);
    },

    /** [v0.48] 发送任意控制帧。 */
    sendCommand(frame: Record<string, unknown>): void {
      sendRaw(frame as OutboundCommand);
    },

    /** [v1.0.23.6] 增量注入后抬升水位（幂等防线第一道闸，见接口注释）。 */
    noteIncremental(projectId: string, lastSeq: number): void {
      if (!projectId || !Number.isFinite(lastSeq) || lastSeq < 0) return;
      setWatermark(projectId, Math.max(getWatermark(projectId), lastSeq));
    },

    get watermarks() {
      return { ..._watermarks };
    },

    get status() {
      return _status;
    },

    _debugReadyState() {
      return ws ? ws.readyState : 'no-ws';
    },

    _getHandshakeBuffer() {
      return handshakeBuffer;
    },

    _getPendingEchoes() {
      return { ..._pendingEchoes };
    },
  };
}
