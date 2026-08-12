/**
 * FakeKnoweServer.ts — 测试用假后端（v0.3 全量重建）
 *
 * 存在的理由：**真后端给不了故障。**
 *   服务重启、ring 淘汰、seq 空洞、广播失聪、双解决——这些恰恰是我们最需要测的，
 *   而在真后端上复现它们要么不可能，要么要靠 sleep 和运气。
 *   所以有这么一台可编排的假后端：想让它什么时候崩就什么时候崩。
 *
 * 铁律：**它必须和真后端说同一种话。**
 *   一台会漂移的夹具比没有夹具更坏——它会让测试绿着，产品红着。
 *   所以 parity.test.ts 会拿前端真实的 Zod（envelope.ts）去校验它吐出的每一条事件，
 *   语义（per-project seq / 无 seq 白名单 / 快照消耗 seq / ring 淘汰报 resync）
 *   逐条对着 PROTOCOL.md 和 backend/ 的实现写。
 *
 * 依赖：只有 `ws`（Node 侧 WebSocket）。不进产品包，只在测试里用。
 */

import { WebSocketServer, WebSocket, type RawData } from 'ws';
import i18n from '../i18n';

// ═══════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════

export type Json = Record<string, unknown>;

/** 无 seq 白名单（与后端 contract.py 的 NO_SEQ_EVENT_TYPES 一字不差） */
export const NO_SEQ_TYPES: ReadonlySet<string> = new Set([
  'project_created',
  'pong',
  'replay_complete',
  'resync_required',
]);

/** 结构事件（进快照 conversation；与后端 STRUCTURAL_EVENT_TYPES 一致） */
export const STRUCTURAL_TYPES: ReadonlySet<string> = new Set([
  'message',
  'approval_card',
  'approval_resolved',
  'agents_created',
  'instruction_injected',
  'report_submitted',
  'error',
  'recovery_notice',
  'user_echo',
]);

export interface FakeServerOptions {
  /** 0 = 让操作系统挑一个空闲端口（测试并行时必须这么用） */
  port?: number;
  /** 每项目 ring 容量。调小可以在 5 条事件内造出淘汰 */
  ringMax?: number;
  /** 收到首帧 replay_request 前的等待窗口，超时发 replay_complete{last_seq:0} */
  handshakeTimeoutMs?: number;
  /** [v1.0.24.4] replay_complete 附带的权威活动账本（测试账本转交用） */
  replayActivity?: Json[];
}

interface ProjectState {
  id: string;
  name: string;
  seq: number;
  ring: Json[];
  evicted: boolean;
}

interface ClientState {
  ws: WebSocket;
  id: string;
  sentProjects: Set<string>;
  handshakeDone: boolean;
}

/** 可编排的故障脚本 */
export type ScriptStep =
  | { kind: 'emit'; projectId: string; event: Json; delayMs?: number }
  | { kind: 'emitNoSeq'; event: Json; delayMs?: number }
  /** 断开所有连接（模拟 server 崩溃），seq 与 ring 保留 */
  | { kind: 'crash'; delayMs?: number }
  /** 清空 seq 与 ring（模拟 server 重启后新纪元）——前端应识别为 epoch reset */
  | { kind: 'restart'; delayMs?: number }
  /** 静音：收到指令照收，但一条事件都不广播（模拟 BUG-1 广播失聪） */
  | { kind: 'mute'; delayMs?: number }
  | { kind: 'unmute'; delayMs?: number }
  /** 凭空跳过 N 个 seq（制造空洞），前端应触发 request_snapshot */
  | { kind: 'gap'; projectId: string; amount?: number; delayMs?: number }
  /** 同一张卡先后发两条 resolution（前端应「首个解决为准」） */
  | { kind: 'doubleResolve'; projectId: string; cardId: string;
      first?: string; second?: string; delayMs?: number }
  | { kind: 'wait'; delayMs: number };

// ═══════════════════════════════════════════════════════════════
// FakeKnoweServer
// ═══════════════════════════════════════════════════════════════

export class FakeKnoweServer {
  private wss: WebSocketServer | null = null;
  private clients = new Set<ClientState>();
  private projects = new Map<string, ProjectState>();
  private clientCounter = 0;
  private cardCounter = 0;

  private readonly ringMax: number;
  private readonly handshakeTimeoutMs: number;
  private readonly wantPort: number;

  /** 静音中——收指令但不广播（模拟广播失聪） */
  muted = false;

  /** 服务端收到过的所有入站指令（断言用） */
  readonly inbound: Json[] = [];

  /** 实际监听端口（port=0 时由系统分配，start() 之后才有值） */
  port = 0;

  /** [v1.0.24.4] replay_complete 携带的权威活动账本（测试注入） */
  replayActivity: Json[] | undefined = undefined;

  constructor(opts: FakeServerOptions = {}) {
    this.wantPort = opts.port ?? 0;
    this.ringMax = opts.ringMax ?? 200;
    this.handshakeTimeoutMs = opts.handshakeTimeoutMs ?? 5000;
    // [v1.0.24.4] 账本注入（测试用），缺省不带字段 = 等价旧后端。
    this.replayActivity = opts.replayActivity;
  }

  get url(): string {
    return `ws://127.0.0.1:${this.port}`;
  }

  // ── 生命周期 ──

  start(): Promise<void> {
    return new Promise((resolve, reject) => {
      const wss = new WebSocketServer({ host: '127.0.0.1', port: this.wantPort });
      this.wss = wss;
      wss.on('error', reject);
      wss.on('listening', () => {
        const addr = wss.address();
        this.port = typeof addr === 'object' && addr ? addr.port : this.wantPort;
        resolve();
      });
      wss.on('connection', (ws) => this.onConnection(ws));
    });
  }

  async stop(): Promise<void> {
    for (const c of this.clients) {
      try { c.ws.close(); } catch { /* 已经断了 */ }
    }
    this.clients.clear();
    const wss = this.wss;
    this.wss = null;
    if (!wss) return;
    await new Promise<void>((resolve) => wss.close(() => resolve()));
  }

  // ── 项目 / seq / ring ──

  private project(projectId: string, name?: string): ProjectState {
    let p = this.projects.get(projectId);
    if (!p) {
      p = { id: projectId, name: name ?? projectId, seq: 0, ring: [], evicted: false };
      this.projects.set(projectId, p);
    } else if (name) {
      p.name = name;
    }
    return p;
  }

  lastSeq(projectId: string): number {
    return this.projects.get(projectId)?.seq ?? 0;
  }

  ringOf(projectId: string): Json[] {
    return [...(this.projects.get(projectId)?.ring ?? [])];
  }

  /**
   * 引擎级事件：盖 seq、注入 project_id/project_name/ts、进 ring、广播。
   * 与后端 hub.emit 一一对应（连 user_echo 不带 project_name 这个细节都一样）。
   */
  emit(projectId: string, payload: Json): Json {
    if (NO_SEQ_TYPES.has(payload.type as string)) {
      throw new Error(`${payload.type} 属无 seq 白名单，不能走 emit()`);
    }
    const p = this.project(projectId);
    p.seq += 1;

    const event: Json = {
      ...payload,
      project_id: projectId,
      ts: new Date().toISOString(),
      seq: p.seq,
    };
    if (payload.type !== 'user_echo') event.project_name = p.name;

    p.ring.push(event);
    if (p.ring.length > this.ringMax) {
      p.ring.splice(0, p.ring.length - this.ringMax);
      p.evicted = true;
    }

    this.broadcast(event);
    return event;
  }

  /** 服务器级事件：不盖 seq、不进 ring、直接广播 */
  emitNoSeq(event: Json): Json {
    this.broadcast(event);
    if (event.type === 'project_created') {
      const pid = event.project_id as string;
      for (const c of this.clients) c.sentProjects.add(pid);
    }
    return event;
  }

  /** 快照：本身消耗一个 seq 并进 ring（PROTOCOL §e） */
  snapshot(projectId: string): Json {
    const p = this.project(projectId);
    const conversation = p.ring.filter((e) => STRUCTURAL_TYPES.has(e.type as string));
    const lastSeq = p.seq;
    p.seq += 1;

    const snap: Json = {
      type: 'state_snapshot',
      project_id: projectId,
      last_seq: lastSeq,
      agents: [],
      conversation,
      pending_card: null,
      ts: new Date().toISOString(),
      seq: p.seq,
    };
    p.ring.push(snap);
    this.broadcast(snap);
    return snap;
  }

  private broadcast(event: Json): void {
    if (this.muted) return;                    // ★ 静音 = 广播失聪
    const raw = JSON.stringify(event);
    for (const c of this.clients) {
      if (c.ws.readyState === WebSocket.OPEN) c.ws.send(raw);
    }
  }

  private sendTo(c: ClientState, event: Json): void {
    if (c.ws.readyState === WebSocket.OPEN) c.ws.send(JSON.stringify(event));
  }

  // ── 连接与握手 ──

  private onConnection(ws: WebSocket): void {
    this.clientCounter += 1;
    const client: ClientState = {
      ws,
      id: `fc${this.clientCounter}`,
      sentProjects: new Set(),
      handshakeDone: false,
    };
    this.clients.add(client);

    const timer = setTimeout(() => {
      if (!client.handshakeDone) {
        client.handshakeDone = true;
        this.sendTo(client, { type: 'replay_complete', last_seq: 0 });  // 超时分支：无 project_id
      }
    }, this.handshakeTimeoutMs);

    ws.on('message', (raw: RawData) => {
      let msg: Json;
      try {
        msg = JSON.parse(String(raw)) as Json;
      } catch {
        this.sendTo(client, { type: 'error', message: i18n.t('fake.knowe.server.02') });   // 服务器级 error：无 seq
        return;
      }
      this.inbound.push(msg);
      this.onCommand(client, msg, timer);
    });

    ws.on('close', () => {
      clearTimeout(timer);
      this.clients.delete(client);
    });
    ws.on('error', () => { /* 测试里断连是家常便饭 */ });
  }

  private onCommand(client: ClientState, msg: Json, timer: NodeJS.Timeout): void {
    switch (msg.type) {
      case 'replay_request': {
        clearTimeout(timer);
        if (!client.handshakeDone) {
          client.handshakeDone = true;
          this.doReplay(client, msg);
        }
        return;
      }

      case 'ping':
        this.sendTo(client, { type: 'pong' });
        return;

      case 'user_message': {
        const pid = (msg.project_id as string) || 'demo';
        this.emit(pid, {
          type: 'user_echo',
          content: msg.content ?? '',
          client_msg_id: (msg.client_msg_id as string) ?? null,
        });
        return;
      }

      case 'create_project': {
        const pid = msg.project_id as string;
        const p = this.project(pid, msg.project_name as string);
        this.emitNoSeq({ type: 'project_created', project_id: pid, project_name: p.name });
        return;
      }

      case 'request_snapshot':
        this.snapshot((msg.project_id as string) || 'demo');
        return;

      case 'approve':
      case 'reject': {
        const pid = (msg.project_id as string) || 'demo';
        this.emit(pid, {
          type: 'approval_resolved',
          card_id: msg.approval_id as string,
          resolution: msg.type === 'approve' ? 'approved' : 'rejected',
        });
        return;
      }

      default:
        // 未知指令 → 服务器级 error（无 seq，前端进全局通知）
        this.sendTo(client, { type: 'error', message: `未知指令：${String(msg.type)}` });
    }
  }

  private doReplay(client: ClientState, msg: Json): void {
    const pid = (msg.project_id as string) || 'demo';
    const since = Number(msg.since_seq ?? 0);
    const p = this.project(pid);

    // 补发这个客户端没收过的 project_created
    for (const [id, proj] of this.projects) {
      if (!client.sentProjects.has(id)) {
        this.sendTo(client, { type: 'project_created', project_id: id, project_name: proj.name });
        client.sentProjects.add(id);
      }
    }

    // ★ ring 淘汰 → 不给残缺历史，改发 resync_required（与后端 B-5 的修法一致）
    const oldest = p.ring.length > 0 ? (p.ring[0]!.seq as number) : null;
    const gap = oldest !== null && oldest > since + 1;

    if (gap) {
      this.sendTo(client, {
        type: 'resync_required',
        last_seq: p.seq,
        message: i18n.t('fake.knowe.server.01'),
      });
    } else {
      for (const e of p.ring) {
        if ((e.seq as number) > since) this.sendTo(client, e);
      }
    }

    const complete: Json = { type: 'replay_complete', project_id: pid, last_seq: p.seq };
    // [v1.0.24.4] 测试可注入账本：无配置则不带字段（等价旧后端）。
    if (this.replayActivity) complete.activity = this.replayActivity;
    this.sendTo(client, complete);
  }

  // ═══════════════════════════════════════════════════════════
  // 故障注入
  // ═══════════════════════════════════════════════════════════

  /**
   * 崩溃：**不打招呼就断**（terminate，不是 close）——真崩溃没有挥手道别。
   * seq / ring 保留，所以重连后应该能正常增量回放。
   *
   * 注：这里不能用 ws.close(1006)——1006 是保留码，协议不允许显式发送，
   *   连接不会真断，wss.close() 会挂死等它。
   */
  crash(): void {
    for (const c of this.clients) {
      try { c.ws.terminate(); } catch { /* 已经断了 */ }
    }
    this.clients.clear();
  }

  /**
   * 重启：seq 归零、ring 清空（**新纪元**）。
   * 前端收到 replay_complete.last_seq < 本地水位 → 应清会话 + 拉快照。
   */
  restart(): void {
    this.crash();
    for (const p of this.projects.values()) {
      p.seq = 0;
      p.ring = [];
      p.evicted = false;
    }
  }

  /** 凭空跳过 N 个 seq —— 制造空洞，前端应触发 request_snapshot */
  gap(projectId: string, amount = 1): void {
    this.project(projectId).seq += amount;
  }

  /** 同一张卡先后发两条 resolution —— 前端应「首个解决为准」 */
  doubleResolve(projectId: string, cardId: string,
                first = 'timeout', second = 'cancelled'): void {
    this.emit(projectId, { type: 'approval_resolved', card_id: cardId, resolution: first });
    this.emit(projectId, { type: 'approval_resolved', card_id: cardId, resolution: second });
  }

  /** 发一张组队审批卡（返回 card_id，方便测试接着 approve） */
  proposeAgents(projectId: string, proposed: { id: string; role: string }[] = [
    { id: 'fe_1', role: i18n.t('common.05') },
  ], ttlMs = 300_000): string {
    this.cardCounter += 1;
    const cardId = `ap_fake_${this.cardCounter}`;
    this.emit(projectId, {
      type: 'approval_card',
      agent_id: 'coordinator',
      tool: 'propose_agents',
      card_id: cardId,
      card: {
        status: 'pending_approval',
        expires_at: new Date(Date.now() + ttlMs).toISOString(),
        approval_id: cardId,
        proposed,
      },
    });
    return cardId;
  }

  /** 演一句话：thinking → deltas → message 收尾 */
  say(projectId: string, agentId: string, text: string, chunk = 4): void {
    this.emit(projectId, { type: 'agent_thinking', agent_id: agentId });
    for (let i = 0; i < text.length; i += chunk) {
      this.emit(projectId, {
        type: 'stream_delta', agent_id: agentId, content: text.slice(i, i + chunk),
      });
    }
    this.emit(projectId, { type: 'message', agent_id: agentId, content: text });
  }

  // ═══════════════════════════════════════════════════════════
  // 脚本编排
  // ═══════════════════════════════════════════════════════════

  async runScript(steps: ScriptStep[]): Promise<void> {
    for (const step of steps) {
      if (step.delayMs) await sleep(step.delayMs);
      switch (step.kind) {
        case 'emit': this.emit(step.projectId, step.event); break;
        case 'emitNoSeq': this.emitNoSeq(step.event); break;
        case 'crash': this.crash(); break;
        case 'restart': this.restart(); break;
        case 'mute': this.muted = true; break;
        case 'unmute': this.muted = false; break;
        case 'gap': this.gap(step.projectId, step.amount ?? 1); break;
        case 'doubleResolve':
          this.doubleResolve(step.projectId, step.cardId, step.first, step.second);
          break;
        case 'wait': break;   // delayMs 已经在上面睡过了
      }
    }
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
