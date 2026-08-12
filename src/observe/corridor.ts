/**
 * corridor.ts — 诊断走廊内核（v0.3 全量重建）
 *
 * 一句话：**系统里每一次「丢弃 / 拒收 / 失败 / 告警」都必须经过这里留下痕迹。**
 * 静默的丢弃是 v0.2 最贵的一课——界面少了一块，没人知道是谁吞的。
 *
 * ⚠ 重建时发现的真问题：旧版走廊的四个计数器**永远是 0**。
 *   transport/socket.ts 里每个丢弃点只写了 console.warn，注释挂着「将来进走廊」，
 *   而那个「将来」没来过。一块永远归零的仪表盘比没有仪表盘更坏——它让人以为一切正常。
 *   这次给 transport 加了一条 onDiagnostic 回调（socket.ts 里搜 [v0.3-走廊]），
 *   由 App.tsx 接到这里。计数器现在会真的跳（有测试证明）。
 *
 * 零依赖、零框架：Node 里能直接跑。
 */

// ═══════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════

export type EventDir = 'in' | 'out';

/**
 * 判定 —— 一条事件在管道里的下场
 *
 *  applied   正常应用
 *  bypass    无 seq 白名单事件走旁路（project_created / pong / replay_complete / ...）
 *  buffered  握手窗口内先进缓冲
 *  dup       seq ≤ 水位 → 重复，丢弃
 *  gap       seq > 水位+1 → 中间有洞，请求快照
 *  rejected  Zod 拒收（字段漂移）
 *  sentinel  回声哨兵告警（5s 没等到 user_echo → 疑似广播失聪）
 *  epoch     纪元重置（server 重启）
 *  failed    出站失败（连接不在 live）
 */
export type EventVerdict =
  | 'applied'
  | 'bypass'
  | 'buffered'
  | 'dup'
  | 'gap'
  | 'rejected'
  | 'sentinel'
  | 'epoch'
  | 'failed';

export interface CorridorEntry {
  /** ISO 8601 UTC */
  ts: string;
  dir: EventDir;
  type: string;
  /** 项目 ID；未知时 '-' */
  projectId: string;
  /** seq；无 seq 事件为 -1 */
  seq: number;
  summary: string;
  verdict: EventVerdict;
}

/** 只读快照（UI 订阅这个，不直接碰内部数组） */
export interface CorridorState {
  entries: CorridorEntry[];
  /** Zod 拒收数（字段漂移的唯一证据） */
  zodRejected: number;
  /** seq 丢弃数（dup + gap） */
  seqDropped: number;
  /** 出站失败数（连接不在 live 时发消息） */
  outboundFailed: number;
  /** 哨兵告警数（回声超时 → 疑似广播失聪） */
  sentinelAlerts: number;
  /** 纪元重置次数（server 重启） */
  epochResets: number;
}

/** 传输层 → 走廊的诊断口（socket.ts 的 onDiagnostic 发这个形状） */
export interface Diagnostic {
  dir?: EventDir;
  type: string;
  projectId?: string;
  seq?: number;
  verdict: EventVerdict;
  summary?: string;
}

export const CORRIDOR_MAX = 300;

// ═══════════════════════════════════════════════════════════════
// 内核
// ═══════════════════════════════════════════════════════════════

type Listener = (s: CorridorState) => void;

const entries: CorridorEntry[] = [];
const counters = {
  zodRejected: 0,
  seqDropped: 0,
  outboundFailed: 0,
  sentinelAlerts: 0,
  epochResets: 0,
};
const listeners = new Set<Listener>();

/** 不可变快照——UI 每次拿到新引用，React 才会重渲染 */
function snapshot(): CorridorState {
  return { entries: [...entries], ...counters };
}

function notify(): void {
  const s = snapshot();
  for (const fn of listeners) fn(s);
}

/** 判定 → 计数器的唯一映射表。别处不许再写一份。 */
function bump(verdict: EventVerdict): void {
  switch (verdict) {
    case 'rejected': counters.zodRejected += 1; break;
    case 'dup':
    case 'gap': counters.seqDropped += 1; break;
    case 'failed': counters.outboundFailed += 1; break;
    case 'sentinel': counters.sentinelAlerts += 1; break;
    case 'epoch': counters.epochResets += 1; break;
    default: break;   // applied / bypass / buffered 不是告警
  }
}

/** 记一条。**全仓库唯一的写入口。** */
export function record(d: Diagnostic): CorridorEntry {
  const entry: CorridorEntry = {
    ts: new Date().toISOString(),
    dir: d.dir ?? 'in',
    type: d.type,
    projectId: d.projectId || '-',
    seq: typeof d.seq === 'number' ? d.seq : -1,
    verdict: d.verdict,
    summary: d.summary ?? '',
  };

  entries.push(entry);
  if (entries.length > CORRIDOR_MAX) {
    entries.splice(0, entries.length - CORRIDOR_MAX);
  }

  bump(d.verdict);
  notify();
  return entry;
}

export function getCorridorState(): CorridorState {
  return snapshot();
}

/** 订阅。返回退订函数。 */
export function subscribeCorridor(fn: Listener): () => void {
  listeners.add(fn);
  fn(snapshot());
  return () => { listeners.delete(fn); };
}

export function resetCorridor(): void {
  entries.length = 0;
  counters.zodRejected = 0;
  counters.seqDropped = 0;
  counters.outboundFailed = 0;
  counters.sentinelAlerts = 0;
  counters.epochResets = 0;
  notify();
}

/** 有没有任何告警计数器亮了（走廊入口徽章的判据） */
export function hasAlerts(): boolean {
  return (
    counters.zodRejected + counters.seqDropped
    + counters.outboundFailed + counters.sentinelAlerts > 0
  );
}

/** 失败报告的核心：一键导出 JSON */
export function exportCorridorJSON(): string {
  return JSON.stringify(
    {
      exportedAt: new Date().toISOString(),
      userAgent: typeof navigator === 'undefined' ? 'node' : navigator.userAgent,
      counters: { ...counters },
      entries: [...entries],
    },
    null,
    2,
  );
}
