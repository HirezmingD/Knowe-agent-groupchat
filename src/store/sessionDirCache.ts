/**
 * [v1.0.39] 会话目录缓存（localStorage）。
 *
 * 作用（微信式启动秒出）：启动时 store 还是空的，群列表却要等 WS 握手后
 * 后端逐条补发 project_created 才出现——这段时间左侧栏一片空白，用户以为
 * 数据被清了。本模块把「会话目录」（有哪些群、群名、顺序、分组状态、花名册、
 * 私聊列表）持久化到 localStorage，store 创建时**同步**读回 populate，
 * 首帧渲染即满；WS 握手后由对账静默修正差异（增量校正，不整树重建）。
 *
 * 边界（架构原则）：
 * · **不缓存消息本体**——聊天记录权威在后端 JSONL，前端绝不复制全量（架构设计 §3.1）。
 * · 缓存只是「启动回显快照」；任何时刻以服务端为最终权威。
 * · 缓存损坏/过期/丢失 → 静默丢弃，完全回退现状流程（可重建原则：慢，不是错）。
 * · 未读数不缓存（首帧一律 0），以服务端 unread_count 为准覆盖——避免红点闪一下消失。
 * · 与 skeletonCache（知知.skeleton.v1.*，行高/滚动/已读水位）独立 key 前缀，互不干扰。
 */

const SCHEMA_VERSION = 1;
const KEY = 'knowe.sessionDir.v1';   // 前缀含版本，升级即换 key 天然隔离

/** 花名册成员骨架（与 registerMember 消费形状对齐）。role 存原始值，populate 时走 registerMember 中文化。 */
export interface CachedMember {
  id: string;
  role: string;
  name?: string;
  avatar?: string;
}

/** 群会话目录条目。数组顺序 = 最近一次会话顺序。 */
export interface CachedProjectEntry {
  projectId: string;
  projectName: string;
  projectDir?: string;
  pinned: boolean;
  folded: boolean;
  muted: boolean;
  pinned_at: number;
  members: CachedMember[];
}

/** 私聊（DM）目录条目。不进 projectOrder，只恢复会话投影。 */
export interface CachedDmEntry {
  sessionId: string;
  projectId: string;
  agentId: string;
  displayName: string;
}

export interface SessionDirCache {
  schemaVersion: number;
  savedAt: number;
  projects: CachedProjectEntry[];
  dm: CachedDmEntry[];
  activeView: string;
}

/** 默认空缓存（兼容无历史/首装）。 */
export function emptySessionDir(): SessionDirCache {
  return { schemaVersion: SCHEMA_VERSION, savedAt: 0, projects: [], dm: [], activeView: 'projects' };
}

/** 读取缓存；损坏/版本不符 → null（调用方走现状流程）。 */
export function loadSessionDir(): SessionDirCache | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SessionDirCache>;
    if (!parsed || parsed.schemaVersion !== SCHEMA_VERSION) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : 0,
      projects: Array.isArray(parsed.projects) ? parsed.projects : [],
      dm: Array.isArray(parsed.dm) ? parsed.dm : [],
      activeView: typeof parsed.activeView === 'string' ? parsed.activeView : 'projects',
    };
  } catch {
    return null;   // localStorage 异常 → 不缓存，走全量重建
  }
}

/** 写入缓存（整体序列化；失败静默——可重建原则）。 */
export function saveSessionDir(cache: SessionDirCache): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...cache, schemaVersion: SCHEMA_VERSION, savedAt: Date.now() }));
  } catch {
    // 容量满/禁用 → 静默降级（下次启动走现状流程，不是错误）
  }
}

let _saveTimer: ReturnType<typeof setTimeout> | null = null;
let _dirty = false;

/**
 * 防抖写：短时间多次变更（建群/改名/置顶/顺序变化）只落一次盘。
 * 调用方在「列表有变化」的任意事件后调用；内部合并 800ms。
 */
export function scheduleSessionDirSave(build: () => SessionDirCache): void {
  _dirty = true;
  if (_saveTimer) return;
  _saveTimer = setTimeout(() => {
    _saveTimer = null;
    if (!_dirty) return;
    _dirty = false;
    saveSessionDir(build());
  }, 800);
}

/** 立即写（应用退出前兜底）。 */
export function flushSessionDir(build: () => SessionDirCache): void {
  if (_saveTimer) {
    clearTimeout(_saveTimer);
    _saveTimer = null;
  }
  _dirty = false;
  saveSessionDir(build());
}

/** 清空缓存（数据被清/诊断用）。 */
export function clearSessionDir(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // 忽略
  }
}