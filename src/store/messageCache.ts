/**
 * [v1.0.39-B3] 消息层本地缓存（localStorage）——「启动秒出」的聊天记录部分。
 *
 * 目录缓存（sessionDirCache）管「有哪些群 / 谁在群里」；本模块管「群里的聊天记录」。
 * 微信式启动：打开软件点进群，聊天记录立即显示本地缓存（秒出），后台快照到达后
 * 自然整体覆盖（快照是服务端权威重建，缓存只是「上次看到的映像」）。
 *
 * 边界（架构原则，与 sessionDirCache 同源）：
 * · 只缓存最近 CACHE_MSG_LIMIT 条**落定**消息（streaming 中的不缓存——没写完的不能当历史）；
 * · 审批卡不缓存（快照重建时自然出现，缓存形状撑不起卡片状态机）；
 * · 服务端永远是权威：快照到达即整体重建覆盖，缓存不做增量对账（无需 diff，简单可靠）；
 * · 缓存损坏/容量满 → 静默丢弃/降级（可重建原则：慢，不是错）；
 * · 与目录缓存独立 key：消息高频变更不拖累目录的 800ms 防抖写。
 */

const SCHEMA_VERSION = 1;
const KEY = 'knowe.sessionMsg.v1';   // 前缀含版本，升级即换 key 天然隔离

/** 每个会话最多缓存的消息条数（够首屏 + 少量滚动；快照全量下发时自然补齐更多历史）。 */
export const CACHE_MSG_LIMIT = 50;

/** 缓存的消息骨架：只存渲染一个气泡所需的字段，不复制完整 Item（体积控制）。 */
export interface CachedMessage {
  kind: 'user' | 'agent' | 'system';
  text: string;
  /** 事件 seq——快照对账/滚动定位/跳转出处的锚点，必须带。 */
  seq?: number;
  ts?: number;
  agentId?: string;
  cmid?: string;
  reasoning?: string;
  reasoningSeconds?: number;
  level?: 'info' | 'error';
}

export interface MessageCache {
  schemaVersion: number;
  savedAt: number;
  /** projectId → 最近 N 条落定消息（时间正序）。 */
  byProject: Record<string, CachedMessage[]>;
}

/** 默认空缓存（兼容无历史/首装）。 */
export function emptyMessageCache(): MessageCache {
  return { schemaVersion: SCHEMA_VERSION, savedAt: 0, byProject: {} };
}

/** 读取缓存；损坏/版本不符 → null（调用方走现状流程，不阻塞启动）。 */
export function loadMessageCache(): MessageCache | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MessageCache>;
    if (!parsed || parsed.schemaVersion !== SCHEMA_VERSION) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : 0,
      byProject: parsed.byProject && typeof parsed.byProject === 'object' ? parsed.byProject : {},
    };
  } catch {
    return null;   // localStorage 异常 → 不缓存，走全量重建
  }
}

/** 写入缓存（整体序列化；失败静默——可重建原则）。 */
export function saveMessageCache(cache: MessageCache): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...cache, schemaVersion: SCHEMA_VERSION, savedAt: Date.now() }));
  } catch {
    // 容量满/禁用 → 静默降级（下次启动走现状流程，不是错误）
  }
}

let _saveTimer: ReturnType<typeof setTimeout> | null = null;
let _dirty = false;

/**
 * 防抖写：流式期间每秒几十条事件，只落一次盘。
 * 防抖 1200ms（比目录缓存略长——消息变更频率远高于列表变更）。
 * 调用方在「任意消息类事件应用后」调用；build 回调在真正落盘时才执行（读最新 state）。
 */
export function scheduleMessageSave(build: () => MessageCache): void {
  _dirty = true;
  if (_saveTimer) return;
  _saveTimer = setTimeout(() => {
    _saveTimer = null;
    if (!_dirty) return;
    _dirty = false;
    saveMessageCache(build());
  }, 1200);
}

/** 立即写（应用退出前兜底）。 */
export function flushMessageCache(build: () => MessageCache): void {
  if (_saveTimer) {
    clearTimeout(_saveTimer);
    _saveTimer = null;
  }
  _dirty = false;
  saveMessageCache(build());
}

/** 清空缓存（数据被清/诊断用）。 */
export function clearMessageCache(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // 忽略
  }
}

// ═══════════════════════════════════════════════════════════════
// 双向转换：store Item ↔ 缓存骨架
// ═══════════════════════════════════════════════════════════════

import type { Conv, Item } from './state';

/** 缓存骨架 → 可渲染 Item（回显用）。审批卡/未知形状返回 null（调用方过滤）。 */
export function cachedToItem(m: CachedMessage): Item | null {
  if (!m || typeof m.text !== 'string' || !m.text) return null;
  if (m.kind === 'user') {
    return {
      kind: 'user',
      text: m.text,
      cmid: m.cmid || `cached-${m.seq ?? Date.now()}`,
      delivery: 'confirmed',   // 缓存消息都是服务端回显过的，直接按已确认渲染
      ts: m.ts,
      seq: m.seq,
    };
  }
  if (m.kind === 'agent') {
    return {
      kind: 'agent',
      agentId: m.agentId || '',
      text: m.text,
      ts: m.ts,
      seq: m.seq,
      reasoning: m.reasoning,
      reasoningSeconds: m.reasoningSeconds,
    };
  }
  return {
    kind: 'system',
    text: m.text,
    level: m.level || 'info',
  };
}

/** Item → 缓存骨架（保存用）。审批卡/流式未落定/无文本 → null（不缓存）。 */
function itemToCached(it: Item): CachedMessage | null {
  if (it.kind === 'approval') return null;              // 审批卡不缓存
  if (it.kind === 'agent' && it.streaming) return null; // 流式未落定不缓存
  if (!it.text) return null;                            // 空气泡（纯文件/活动行）不缓存
  if (it.kind === 'user') {
    return { kind: 'user', text: it.text, seq: it.seq, ts: it.ts, cmid: it.cmid };
  }
  if (it.kind === 'agent') {
    return {
      kind: 'agent', text: it.text, seq: it.seq, ts: it.ts,
      agentId: it.agentId, reasoning: it.reasoning, reasoningSeconds: it.reasoningSeconds,
    };
  }
  return { kind: 'system', text: it.text, level: it.level };
}

/**
 * 从 store 会话树构建缓存：每个会话取最近 CACHE_MSG_LIMIT 条落定消息（时间正序）。
 * 过滤：审批卡 / streaming 中 / 空气泡（无文本）。
 */
export function buildMessageCacheFromConvs(convs: Record<string, Conv>): MessageCache {
  const byProject: Record<string, CachedMessage[]> = {};
  for (const pid of Object.keys(convs)) {
    const items = convs[pid]?.items;
    if (!items || items.length === 0) continue;
    const cached: CachedMessage[] = [];
    for (let i = items.length - 1; i >= 0 && cached.length < CACHE_MSG_LIMIT; i--) {
      const item = items[i];
      if (!item) continue;
      const cm = itemToCached(item);
      if (cm) cached.push(cm);
    }
    if (cached.length === 0) continue;
    cached.reverse();   // 逆序收集后再翻转回时间正序
    byProject[pid] = cached;
  }
  return { schemaVersion: SCHEMA_VERSION, savedAt: Date.now(), byProject };
}
