/**
 * selectors.ts — 唯一合法订阅入口（Claude §3.4 三定律）
 *
 * 定律：
 *   1. 只订阅数据，不订阅方法（禁止 useStore(s => s.getActiveConv())）
 *   2. 订阅最小切片（ChatStream 只订 items 引用，ConnBadge 只订 conn）
 *      immer 结构共享保证"改哪条、哪条引用变"，精准触发
 *   3. 每个可变切片配 RTL 渲染回归（阶段 5 组件层落地）
 *
 * 边界铁律：components 只 import selectors 与 store actions
 *   任何组件直接 import transport 或 contract = lint error
 */

import type { KnoweStore } from './store';
import type { Item, ConnStatus, GlobalNotice } from './state';
import { PLATFORM_PROJECT_ID } from './avatar';
import { parseDmId } from './chat';

// ═══════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════

const EMPTY_ITEMS: Item[] = [];

// ═══════════════════════════════════════════════════════════════
// 选择器
// ═══════════════════════════════════════════════════════════════

/** 当前活跃项目的 items（最小切片——只在 items 引用变化时触发重渲染） */
export function selectActiveItems(s: KnoweStore): Item[] {
  if (!s.activeProjectId) return EMPTY_ITEMS;
  return s.convs[s.activeProjectId]?.items ?? EMPTY_ITEMS;
}

/** 连接状态（只订阅 conn） */
export function selectConn(s: KnoweStore): ConnStatus {
  return s.conn;
}

/** 侧栏列表项 */
export interface WorkingAgentSummary {
  id: string;
  displayName: string;
  role: string;
  busySince: number;
}

export interface ProjectListEntry {
  id: string;
  name: string;
  /** [v0.7 #1] 这个会话还没发出去的字（空串 = 没草稿） */
  draft: string;
  /** 当前正在工作的成员；按最近开工时间降序。 */
  workingAgents: WorkingAgentSummary[];
  /** 项目最近一次进入工作态的时间，用于多个忙碌项目“后来者居上”。 */
  workingSince: number;
}

/**
 * 项目列表（侧栏用）
 *
 * [v0.4] **__platform__ 不是项目** —— 它是知知住的那个特殊会话，
 *   左栏顶上有专门的固定入口。让它混进项目列表，用户就会看到一个叫「知知」的假项目，
 *   还能把它归档、删除——那就荒唐了。
 *
 * [v0.15] 排序有两层临时优先级：**正在工作 > 有草稿 > 正常会话顺序**。
 *   工作项目按最近开工时间“后来者居上”；最后一名 Worker idle 后立即撤销临时置顶。
 *   草稿一发出去也自动撤销。两类临时状态都不改写 projectOrder，所以恢复时不会
 *   留下永久置顶副作用（Array.sort 自 ES2019 起是稳定排序）。
 *
 * [v0.7b #2] 「原顺序」现在会动了：`projectOrder` 由 store 的 bumpProjectOrder 维护——
 *   哪个会话刚有动静（用户发言 / Agent 说话 / 弹了审批卡），哪个就浮到最前面。
 *   **知知（__platform__）不在 projectOrder 里，也就永远不受 bump 影响**——
 *   她被 filter 掉，由 ConvList 硬写在列表最顶上，谁也挤不掉她。
 *
 *   三条规则的优先级：**正在工作 > 草稿 > 最近有动静**。
 *   “正在工作”只是一层 selector 投影；结束后回到草稿/最近消息决定的正常位置。
 */
export function selectProjectList(s: KnoweStore): ProjectListEntry[] {
  const rows = s.projectOrder
    .filter((pid) => pid !== PLATFORM_PROJECT_ID && !parseDmId(pid))
    .map((pid) => {
      const workingAgents = (s.convs[pid]?.members ?? [])
        .filter((m) => m.status !== 'removed' && m.state === 'busy')
        .map((m) => ({
          id: m.id,
          displayName: m.display.name,
          role: m.display.role,
          busySince: m.busySince ?? 0,
        }))
        .sort((a, b) => b.busySince - a.busySince);
      return {
        id: pid,
        name: s.convs[pid]?.projectName || pid,
        draft: s.convs[pid]?.draft ?? '',
        workingAgents,
        workingSince: workingAgents[0]?.busySince ?? 0,
      };
    });

  return rows.sort((a, b) => {
    const rankDiff = projectRank(a) - projectRank(b);
    if (rankDiff !== 0) return rankDiff;
    if (a.workingSince && b.workingSince) return b.workingSince - a.workingSince;
    return 0; // 稳定排序保留 projectOrder 的正常会话顺序
  });
}

function projectRank(p: ProjectListEntry): number {
  if (p.workingAgents.length > 0) return 0;
  if (p.draft.trim()) return 1;
  return 2;
}

/**
 * [v0.7 #1] 某一个会话的草稿。
 *
 * 是个工厂：Composer 用当前 activeProjectId 现做一个选择器，
 * 订阅的切片就精确到「这一个会话的 draft」——别的会话打字不会牵动它重渲染。
 */
export function makeSelectDraft(projectId: string | null): (s: KnoweStore) => string {
  return (s: KnoweStore): string => {
    if (!projectId) return '';
    return s.convs[projectId]?.draft ?? '';
  };
}

/** [v1.0.19.4] 当前会话待发送的附件（归会话，切走保留、切回来还在）。 */
const EMPTY_ATTACHMENTS: import('./state').AttachmentInput[] = [];
export function makeSelectAttachments(
  projectId: string | null,
): (s: KnoweStore) => import('./state').AttachmentInput[] {
  return (s: KnoweStore): import('./state').AttachmentInput[] => {
    if (!projectId) return EMPTY_ATTACHMENTS;
    return s._attachments[projectId] ?? EMPTY_ATTACHMENTS;
  };
}

/** 当前是不是在跟知知说话 */
export function selectIsPlatform(s: KnoweStore): boolean {
  return s.activeProjectId === PLATFORM_PROJECT_ID;
}

/** 当前活跃项目名称 */
export function selectActiveConvName(s: KnoweStore): string {
  if (!s.activeProjectId) return '';
  const sessionId = s.activeProjectId;
  const convName = s.convs[sessionId]?.projectName;
  const dm = parseDmId(sessionId);
  if (!dm) return convName || sessionId;
  const memberName = s.convs[dm.projectId]?.members
    .find((member) => member.id === dm.agentId)?.display.name;
  if (memberName) return memberName;
  return convName && convName !== sessionId ? convName : dm.agentId;
}

/** 全局通知（无 project_id 的服务器级错误） */
export function selectNotices(s: KnoweStore): GlobalNotice[] {
  return s.notices;
}

/**
 * [v0.37] 成员排序（花名册 / 群内私聊成员视图共用一处）。
 *
 * 规则：没人忙时「项目经理第一 + 加入顺序」；有人忙时忙碌成员按最近开工时间靠前，
 * 已归档的一律沉底。抽出来是因为群内私聊的左栏成员视图要按**同一个顺序**排
 * （PROMPT §2.2「项目经理第一 → 忙碌的 → 加入顺序」），不能各排各的。
 */
/**
 * 排序缓存：同一次输入数组引用 → 复用上一次的排序结果。
 *
 * [v1.0.23.13] ★ 不缓存的话，selectActiveMembers 在 busy/standby 成员存在时
 *   每次都返回新数组（[...].sort()）→ ChatStream 每次重渲染都拿到新引用 →
 *   传给 MessageBubble 的 face 对象/回调全部重建 → 击穿 React.memo →
 *   整条消息流重渲染 → 切会话/拖窗口卡顿。这里把排序结果按输入引用缓存：
 *   convs[pid].members 是 immer 管理的，只有真变化才换引用 → 排序结果随之稳定。
 */
const rosterSortCache = new WeakMap<import('./state').Member[], import('./state').Member[]>();

export function orderRosterMembers(members: import('./state').Member[]): import('./state').Member[] {
  if (!members.some((m) => m.state !== 'idle' && m.status !== 'removed')) return members;

  const cached = rosterSortCache.get(members);
  if (cached) return cached;

  const original = new Map(members.map((m, i) => [m.id, i]));
  const sorted = [...members].sort((a, b) => {
    const aArchived = a.status === 'removed';
    const bArchived = b.status === 'removed';
    if (aArchived !== bArchived) return aArchived ? 1 : -1;

    const rank = (m: import('./state').Member): number =>
      m.state === 'busy' ? 0 : m.state === 'standby' ? 1 : 2;
    const aRank = aArchived ? 3 : rank(a);
    const bRank = bArchived ? 3 : rank(b);
    if (aRank !== bRank) return aRank - bRank;
    const aBusy = aRank === 0;
    const bBusy = bRank === 0;
    if (aBusy && bBusy) {
      const timeDiff = (b.busySince ?? 0) - (a.busySince ?? 0);
      if (timeDiff !== 0) return timeDiff;
    }

    // 空闲组仍是"项目经理第一 + 加入顺序"；busy / standby 按占用优先级靠前。
    if (aRank >= 2 && bRank >= 2) {
      if (a.id === 'coordinator' && b.id !== 'coordinator') return -1;
      if (b.id === 'coordinator' && a.id !== 'coordinator') return 1;
    }
    return (original.get(a.id) ?? 0) - (original.get(b.id) ?? 0);
  });
  rosterSortCache.set(members, sorted);
  return sorted;
}

/** 当前活跃项目的成员列表 */
export function selectActiveMembers(s: KnoweStore): import('./state').Member[] {
  if (!s.activeProjectId) return [];
  const members = s.convs[s.activeProjectId]?.members ?? [];
  return orderRosterMembers(members);
}

/** 当前活跃会话的 banner */
export function selectActiveBanner(s: KnoweStore): string | null {
  if (!s.activeProjectId) return null;
  return s.convs[s.activeProjectId]?.banner ?? null;
}

/** 当前活跃项目 ID */
export function selectActiveProjectId(s: KnoweStore): string | null {
  return s.activeProjectId;
}

// ═══════════════════════════════════════════════════════════════
// [v1.0.23.5] 会话视图常驻内存 · per-session 订阅工厂
// 照 makeSelectDraft 模式：ChatStream 多实例化后，每个实例订阅「自己负责的会话」。
// immer 引用隔离保证：只有该会话数据变化时对应实例才重渲染。
// ═══════════════════════════════════════════════════════════════

/** 某会话的 items（照 selectActiveItems）。 */
export function makeSelectItems(projectId: string): (s: KnoweStore) => Item[] {
  return (s: KnoweStore): Item[] => {
    if (!projectId) return EMPTY_ITEMS;
    return s.convs[projectId]?.items ?? EMPTY_ITEMS;
  };
}

/** 某会话的成员列表（照 selectActiveMembers，含排序缓存）。 */
export function makeSelectMembers(projectId: string): (s: KnoweStore) => import('./state').Member[] {
  return (s: KnoweStore): import('./state').Member[] => {
    if (!projectId) return [];
    const members = s.convs[projectId]?.members ?? [];
    return orderRosterMembers(members);
  };
}

/** 某会话的名称（照 selectActiveConvName，含 DM 私聊取成员名逻辑）。 */
export function makeSelectConvName(projectId: string): (s: KnoweStore) => string {
  return (s: KnoweStore): string => {
    if (!projectId) return '';
    const convName = s.convs[projectId]?.projectName;
    const dm = parseDmId(projectId);
    if (!dm) return convName || projectId;
    const memberName = s.convs[dm.projectId]?.members
      .find((member) => member.id === dm.agentId)?.display.name;
    if (memberName) return memberName;
    return convName && convName !== projectId ? convName : dm.agentId;
  };
}

/** 某会话的 banner（照 selectActiveBanner）。 */
export function makeSelectBanner(projectId: string): (s: KnoweStore) => string | null {
  return (s: KnoweStore): string | null => {
    if (!projectId) return null;
    return s.convs[projectId]?.banner ?? null;
  };
}

/** 活跃视图名 */
export function selectActiveView(s: KnoweStore): string {
  return s.activeView;
}

/** Cmd+K 状态 */
export function selectCmdKOpen(s: KnoweStore): boolean {
  return s.cmdKOpen;
}
