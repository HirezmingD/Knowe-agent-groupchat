/**
 * store.ts — Zustand store 壳（v2 · Claude 审计版）
 *
 * 按 Claude §3.3/§3.4 重写。关键改动：
 *   - 删除 lastSeq / projectSeqs / _renderTick（水位只在 transport，tick 禁入 store）
 *   - 落地乐观渲染（sendMessage 立即插入 pending 气泡）
 *   - 适配 SocketAPI v2（callbacks 对象 + 六态 ConnStatus）
 *   - 新增 corridor / notices 状态槽位
 *
 * version: 2
 */

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { InboundEvent, ActivityLedgerEntry } from '../contract/envelope';
import type { SocketAPI } from '../transport/socket';
import { PLATFORM_PROJECT_ID } from './avatar';
import { dmSessionId, dmGroupOf, parseDmId } from './chat';
import { reconcileProjectAlias } from './projectAlias';
import { runtimeHttpBase } from '../shared/runtimeEndpoints';
import { runtimeFetch } from '../shared/runtimeFetch';
import {
  type Conv,
  type UserItem,
  type ConnStatus,
  type AgentRegistry,
  type RoleType,
  type GlobalNotice,
  type ChatQuote,
  type MessageQuote,
  type ForwardItem,
  type ForwardMeta,
  type ProducedFile,
  type AttachmentInput,
  itemKeyOf,
  acknowledgeTransientFrame,
  applyEvent,
  calibrateRosterActivity,
  getConv,
  registerProject,
  getProjectList,
  registerMember,
  humanizeLlmError,
  pendingTransientFrameIds,
  DEFAULT_AGENTS,
  DEFAULT_ROLE_TYPES,
} from './state';
import i18n from '../i18n';

const _transientFrameFallbacks = new Set<string>();

/**
 * 未挂载/后台会话没有 StreamBubble 可回执首帧；双 RAF 后按同一路径确认，既给当前
 * renderer 留出真实 paint 机会，也保证临时态不会永久卡住。测试/SSR 无 RAF 时用约
 * 两帧的定时器兜底。
 */
function afterTwoPaints(callback: () => void): void {
  const raf = typeof globalThis.requestAnimationFrame === 'function'
    ? globalThis.requestAnimationFrame.bind(globalThis)
    : null;
  if (!raf) {
    setTimeout(callback, 34);
    return;
  }
  raf(() => { raf(callback); });
}

function scheduleTransientFrameFallback(projectId: string, frameIds: string[]): void {
  for (const frameId of frameIds) {
    const key = `${projectId}\u0000${frameId}`;
    if (_transientFrameFallbacks.has(key)) continue;
    _transientFrameFallbacks.add(key);
    afterTwoPaints(() => {
      _transientFrameFallbacks.delete(key);
      useKnoweStore.getState().ackTransientFrame(projectId, frameId);
    });
  }
}

/**
 * create_project transport shape.
 *
 * v0.7 added `projectDir`; v0.18 adds the optional `approvalId` correlation field so a canonical
 * frontend id can be bound to the create-project approval before the subsequent approve command.
 */
type CreateProjectFn = (
  projectId: string, projectName: string, projectDir?: string, approvalId?: string,
  roles?: string[],
) => void;

/** [v0.44.8] 后端持久化的群聊列表偏好。project_id 是稳定主键，名称只是可改显示值。 */
export interface ProjectConversationState {
  project_id: string;
  project_name?: string;
  pinned: boolean;
  muted: boolean;
  folded: boolean;
  pinned_at: number;
}

interface ConversationOrderDraft {
  projectOrder: string[];
  pinnedProjects: Record<string, number>;
  mutedProjects: Record<string, true>;
  foldedProjects: Record<string, true>;
  pinnedCollapsed: boolean;
  foldedOpen: boolean;
  convs: Record<string, Conv>;
}

let _conversationSyncTimer: ReturnType<typeof setTimeout> | null = null;
let _conversationSyncInFlight: Promise<void> | null = null;
let _conversationSyncQueued = false;


interface DeleteProgressState {
  projectId: string;
  message: string;
  shown: boolean;
  timer: ReturnType<typeof setTimeout>;
}

const _deleteProgress = new Map<string, DeleteProgressState>();

function deleteOperationId(): string {
  const uuid = globalThis.crypto?.randomUUID?.().replace(/-/g, '');
  return `del_${uuid || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`}`;
}

function beginDeleteProgress(operationId: string, projectId: string): void {
  const state: DeleteProgressState = {
    projectId,
    message: i18n.t('store.16'),
    shown: false,
    timer: setTimeout(() => {
      const current = _deleteProgress.get(operationId);
      if (!current) return;
      current.shown = true;
      useKnoweStore.getState().upsertNotice(operationId, current.message);
    }, 2000),
  };
  _deleteProgress.set(operationId, state);
}

function finishDeleteProgress(operationId: string, message: string): boolean {
  const state = _deleteProgress.get(operationId);
  if (!state) return false;
  clearTimeout(state.timer);
  if (state.shown) useKnoweStore.getState().upsertNotice(operationId, message);
  _deleteProgress.delete(operationId);
  return state.shown;
}

async function projectMenuJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await runtimeFetch(`${runtimeHttpBase()}${path}`, {
    cache: 'no-store',
    ...init,
    headers,
  });
  let payload: unknown = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const row = payload && typeof payload === 'object'
      ? payload as Record<string, unknown>
      : null;
    let message = row && 'error' in row
      ? String(row.error || i18n.t('store.09'))
      : i18n.t('store.httpFailed', { status: response.status });
    const stage = row && typeof row.stage === 'string' ? row.stage.trim() : '';
    // Newer deletion endpoints already include the stage in their prose.  Older or
    // proxy-generated responses may only return the structured field, so add it once.
    if (stage && !message.includes(stage)) message = `${stage}：${message}`;
    throw new Error(message);
  }
  return payload as T;
}

/** 改名后同步所有已存在的会话投影：群名、聊天标题、花名册标题、私聊归属标签共用这些字段。 */
function applyProjectName(
  draft: Pick<ConversationOrderDraft, 'convs'>, projectId: string, projectName?: string,
): void {
  // dm:* 的名字来自所属群花名册，不接受 project_created / project_renamed 把频道 id
  // 当项目名写进来。真正的群改名仍会在下面同步到所有 DM 的 parentProjectName。
  if (parseDmId(projectId)) return;
  const name = projectName?.trim();
  if (!name) return;
  const group = draft.convs[projectId];
  if (group) group.projectName = name;
  for (const conv of Object.values(draft.convs)) {
    if (conv.parentProjectId === projectId) conv.parentProjectName = name;
  }
}

/**
 * DM 不是项目，只是一条挂在群下面的事件频道。所有“自动建会话”入口统一从父群花名册
 * 补齐名字、头像和归属，既修复 getConv() 先到导致的空 projectName，也能覆盖旧版本
 * 写进去的 `dm:project:agent` raw 名字。
 */
function ensureConversation(convs: Record<string, Conv>, sessionId: string): Conv {
  const conv = getConv(convs, sessionId);
  const dm = parseDmId(sessionId);
  if (!dm) return conv;

  const group = convs[dm.projectId];
  const member = group?.members?.find((candidate) => candidate.id === dm.agentId);
  if (member) {
    conv.projectName = member.display.name || dm.agentId;
    const existing = conv.members.find((candidate) => candidate.id === dm.agentId);
    if (existing) {
      existing.status = member.status;
      existing.state = member.state;
      existing.busySince = member.busySince;
      existing.display = { ...member.display };
    } else {
      conv.members.push({ ...member, display: { ...member.display } });
    }
  } else if (!conv.projectName || conv.projectName === sessionId) {
    conv.projectName = dm.agentId;
  }
  conv.parentProjectId = dm.projectId;
  conv.parentProjectName = group?.projectName || dm.projectId;
  return conv;
}

/**
 * 统一排序层：知知不在 projectOrder；项目区恒为「置顶（后来者居上）→ 普通活跃序 → 折叠」。
 * 所有插入、活跃上浮、服务端对账都必须经过这里，普通消息才不可能顶掉置顶群。
 */
function normalizeProjectOrder(draft: ConversationOrderDraft): void {
  const unique: string[] = [];
  const seen = new Set<string>();
  for (const pid of draft.projectOrder) {
    if (!pid || pid === PLATFORM_PROJECT_ID || parseDmId(pid) || seen.has(pid)) continue;
    seen.add(pid);
    unique.push(pid);
  }
  const pinned = unique
    .filter((pid) => Object.prototype.hasOwnProperty.call(draft.pinnedProjects, pid))
    .sort((a, b) => (draft.pinnedProjects[b] || 0) - (draft.pinnedProjects[a] || 0));
  const normal = unique.filter(
    (pid) => !draft.pinnedProjects[pid] && !draft.foldedProjects[pid],
  );
  const folded = unique.filter((pid) => !!draft.foldedProjects[pid]);
  draft.projectOrder.splice(0, draft.projectOrder.length, ...pinned, ...normal, ...folded);
  if (pinned.length < 3) draft.pinnedCollapsed = false;
  if (folded.length === 0) draft.foldedOpen = false;
}

/** 新/恢复项目进列表：普通项目落在置顶区之后，折叠项目永远在最底，置顶按 pinned_at 排。 */
function insertProjectOrder(draft: ConversationOrderDraft, pid: string): void {
  if (!pid || pid === PLATFORM_PROJECT_ID || parseDmId(pid)) return;
  const old = draft.projectOrder.indexOf(pid);
  if (old >= 0) draft.projectOrder.splice(old, 1);
  if (draft.foldedProjects[pid]) {
    draft.projectOrder.push(pid);
  } else if (draft.pinnedProjects[pid]) {
    draft.projectOrder.unshift(pid);
  } else {
    let afterPins = 0;
    while (afterPins < draft.projectOrder.length
           && !!draft.pinnedProjects[draft.projectOrder[afterPins]]) afterPins += 1;
    draft.projectOrder.splice(afterPins, 0, pid);
  }
  normalizeProjectOrder(draft);
}

/**
 * 有动静的会话上浮：muted 仍上浮；folded 完全不动；pinned 只留在置顶区并按置顶时间排序。
 */
function bumpProjectOrder(draft: ConversationOrderDraft, pid: string): void {
  if (!pid || pid === PLATFORM_PROJECT_ID || parseDmId(pid) || draft.foldedProjects[pid]) return;
  const idx = draft.projectOrder.indexOf(pid);
  if (idx < 0) return;
  if (draft.pinnedProjects[pid]) {
    normalizeProjectOrder(draft);
    return;
  }
  draft.projectOrder.splice(idx, 1);
  let afterPins = 0;
  while (afterPins < draft.projectOrder.length
         && !!draft.pinnedProjects[draft.projectOrder[afterPins]]) afterPins += 1;
  draft.projectOrder.splice(afterPins, 0, pid);
}

function parseConversationState(value: unknown): ProjectConversationState | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (typeof row.project_id !== 'string' || !row.project_id) return null;
  return {
    project_id: row.project_id,
    project_name: typeof row.project_name === 'string' ? row.project_name : undefined,
    pinned: !!row.pinned,
    muted: !!row.muted,
    folded: !!row.folded,
    pinned_at: Number.isFinite(Number(row.pinned_at)) ? Math.max(0, Number(row.pinned_at)) : 0,
  };
}

function applyConversationState(
  draft: ConversationOrderDraft, value: ProjectConversationState,
): void {
  const pid = value.project_id;
  if (!pid || pid === PLATFORM_PROJECT_ID || parseDmId(pid)) return;
  const folded = !!value.folded;
  const pinned = !!value.pinned && !folded;
  const pinnedAt = pinned && Number.isFinite(Number(value.pinned_at))
    ? Math.max(1, Number(value.pinned_at)) : 0;

  if (pinned) draft.pinnedProjects[pid] = pinnedAt;
  else delete draft.pinnedProjects[pid];
  if (value.muted) draft.mutedProjects[pid] = true;
  else delete draft.mutedProjects[pid];
  if (folded) draft.foldedProjects[pid] = true;
  else delete draft.foldedProjects[pid];

  applyProjectName(draft, pid, value.project_name);
  const conv = draft.convs[pid];
  // muted / folded 都是「完全不提醒」：立刻清掉旧数字，后续事件入口也不会再累加。
  if (conv && (value.muted || folded)) conv.unread = 0;
  normalizeProjectOrder(draft);
}

/** 乐观项目 id 被后端 canonical id 接管时，菜单状态也跟着迁移，不能留一枚幽灵键。 */
function reconcileConversationStateAlias(
  draft: ConversationOrderDraft, requestProjectId: string, canonicalProjectId: string,
): void {
  if (!requestProjectId || !canonicalProjectId || requestProjectId === canonicalProjectId) return;
  const oldPinned = Object.prototype.hasOwnProperty.call(
    draft.pinnedProjects, requestProjectId,
  ) ? draft.pinnedProjects[requestProjectId] : 0;
  const oldMuted = !!draft.mutedProjects[requestProjectId];
  const oldFolded = !!draft.foldedProjects[requestProjectId];
  const canonicalKnown = Object.prototype.hasOwnProperty.call(
    draft.pinnedProjects, canonicalProjectId,
  ) || !!draft.mutedProjects[canonicalProjectId] || !!draft.foldedProjects[canonicalProjectId];

  delete draft.pinnedProjects[requestProjectId];
  delete draft.mutedProjects[requestProjectId];
  delete draft.foldedProjects[requestProjectId];
  if (!canonicalKnown && (oldPinned || oldMuted || oldFolded)) {
    applyConversationState(draft, {
      project_id: canonicalProjectId,
      pinned: !!oldPinned && !oldFolded,
      muted: oldMuted,
      folded: oldFolded,
      pinned_at: oldPinned,
    });
  } else {
    normalizeProjectOrder(draft);
  }
}

function snapshotConversationState(state: KnoweStore, projectId: string): ProjectConversationState {
  return {
    project_id: projectId,
    project_name: state.convs[projectId]?.projectName,
    pinned: Object.prototype.hasOwnProperty.call(state.pinnedProjects, projectId),
    muted: !!state.mutedProjects[projectId],
    folded: !!state.foldedProjects[projectId],
    pinned_at: state.pinnedProjects[projectId] || 0,
  };
}

/** project_created 是兼容刷新信号；合并短时间内的一串补发，避免开机 N 个项目发 N 次 HTTP。 */
function scheduleConversationStateSync(delay = 80): void {
  if (_conversationSyncTimer) clearTimeout(_conversationSyncTimer);
  _conversationSyncTimer = setTimeout(() => {
    _conversationSyncTimer = null;
    void useKnoweStore.getState().syncConversationStates();
  }, delay);
}

/**
 * [v0.7b #2] 这条事件算不算「这个会话有动静了」？
 *
 * ★ stream_delta 是个例外：它一秒钟来几十条。**只在第一条时算数**——
 *   判据是「这个 agent 现在还没有正在流的气泡」。不这么挡的话，
 *   每个 token 都会把 projectOrder 重建一遍，左栏整列跟着重渲染，手感是黏的。
 *   所以这个判断必须在 applyEvent **之前**做（applyEvent 一跑，气泡就建出来了）。
 */
function isBumpEvent(conv: Conv, event: InboundEvent): boolean {
  switch (event.type) {
    case 'user_echo':
    case 'approval_card':
      return true;
    /*
     * [v0.23 问题四] 空 message **不算动静**。
     *
     *   项目经理用 NOTHING_TO_ADD 沉默 → 后端照样发一条 content='' 的 message
     *   （那是「气泡落定 / 成员收回 idle」的信号，见 engine 的注释）。
     *   老写法把它当成「这个会话有动静了」，于是**一次沉默会把群顶到左栏最上面**——
     *   用户看过去，没有新消息，却排在第一。
     *
     *   ★ 和下面 isUnreadEvent 保持同一个判据：**有字才算说话**。
     *     两处对同一件事给出不同答案，迟早会变成一个查不出来的怪 bug。
     */
    case 'message': {
      const content = (event as InboundEvent & { content?: string }).content;
      return !!(content && content.trim());
    }
    // agent_thinking 不再永久改写 projectOrder：工作态由 selectProjectList 临时置顶，
    // agent_idle 后自然回到正常会话顺序。否则一次开工会把项目永远钉在最上面。
    case 'agent_thinking':
      return false;
    case 'stream_delta': {
      const aid = (event as InboundEvent & { agent_id: string }).agent_id;
      return !conv.items.some(
        (it) => it.kind === 'agent' && it.agentId === aid && !!it.streaming,
      );
    }
    default:
      return false;
  }
}

/**
 * [v0.8b #1] 这条连接上，哪些项目已经把历史要过来了。
 *
 * 不进 store 的 state：它是**传输层的记账**，跟 UI 无关，
 * 放进 immer 只会让每次同步都触发一遍全局重渲染。
 */
const _synced = new Set<string>();

/**
 * [v0.8c #1] 开机要把**每个**项目的快照都要过来 —— 但不能一口气全砸出去。
 *
 *   v0.8b 只在「点进某个群」时要一次。于是开机看到的是一排文字头像，
 *   得挨个点一遍，群成员才跳出来。用户有 20 个群，就得点 20 次。
 *   ——头像不是「点开才有的东西」，它是列表的一部分。
 *
 *   但 20 个 request_snapshot 同时出膛也不行：后端每收一条就要把整条会话投影一遍，
 *   20 条一起来，第一屏该显示的东西反而被排在了后面。
 *   所以排队：当前这个群立刻发（用户正看着它），其余的每 150ms 发一个。
 *   一个 20 群的用户，3 秒内全部到齐，而且服务端一直是从容的。
 */
const SNAPSHOT_STAGGER_MS = 150;
const _snapQueue: string[] = [];
let _snapTimer: ReturnType<typeof setTimeout> | null = null;
let _bulkScheduled = false;

/** 连接断了/重连了 → 之前要过的都不算数了，排队中的也一并作废 */
function resetSynced(): void {
  _synced.clear();
  _snapQueue.length = 0;
  if (_snapTimer) { clearTimeout(_snapTimer); _snapTimer = null; }
  _bulkScheduled = false;
}

function reconcileSnapshotAlias(requestProjectId: string, canonicalProjectId: string): void {
  if (!requestProjectId || requestProjectId === canonicalProjectId) return;
  if (_synced.delete(requestProjectId)) _synced.add(canonicalProjectId);
  for (let i = _snapQueue.length - 1; i >= 0; i -= 1) {
    if (_snapQueue[i] === requestProjectId) _snapQueue[i] = canonicalProjectId;
  }
  for (let i = _snapQueue.length - 1; i >= 0; i -= 1) {
    if (_snapQueue.indexOf(_snapQueue[i]) !== i) _snapQueue.splice(i, 1);
  }
}

function pumpSnapshots(getSocket: () => SocketAPI | null): void {
  if (_snapTimer) return;                     // 队列已经在跑了
  const step = (): void => {
    _snapTimer = null;
    const pid = _snapQueue.shift();
    if (pid === undefined) return;
    const socket = getSocket();
    if (!socket) { _snapQueue.length = 0; return; }
    socket.requestSnapshot(pid);
    if (_snapQueue.length) _snapTimer = setTimeout(step, SNAPSHOT_STAGGER_MS);
  };
  _snapTimer = setTimeout(step, SNAPSHOT_STAGGER_MS);
}

/**
 * 要一次这个项目的快照。**每条连接每个项目只要一次**（_synced 记账）——
 * 因为 state_snapshot 会清空并重建会话的 items：重复要，屏幕会闪，
 * 而且正在飞的乐观气泡（还没等到回声的那条）会被一起抹掉。
 *
 * @param now true = 立刻发（用户正盯着这个群）；false = 进队列，慢慢来
 */
function requestSnapshotOnce(
  projectId: string, getSocket: () => SocketAPI | null, now = false,
): void {
  if (!projectId || _synced.has(projectId)) return;
  const socket = getSocket();
  if (!socket) return;
  _synced.add(projectId);
  if (now) {
    socket.requestSnapshot(projectId);
    return;
  }
  _snapQueue.push(projectId);
  pumpSnapshots(getSocket);
}

/**
 * [v0.8d #5] 这条事件算不算「有新东西要看」？
 *
 * 算的：
 *   · message      —— 别人说话了（空 content 的收尾消息不算，它只是让光标停转）
 *   · approval_card —— 有张卡等着你点头。这比一条消息更要紧。
 *
 * ★ **user_echo 不算。** 那是你自己刚说的话的回声——
 *   为自己说的话给自己记一条未读，是荒谬的。
 *   （提示词里把它列进来了，我没照做，理由就是这一句。）
 *
 * stream_delta / thinking / tool_* 也不算：那是「他在动」，不是「他说完了」。
 * 一条回复流三十秒，红点不该跳三十次。
 */
function isUnreadEvent(event: InboundEvent): boolean {
  if (event.type === 'approval_card') return true;
  if (event.type === 'message') {
    const content = (event as InboundEvent & { content?: string }).content;
    return !!(content && content.trim());
  }
  return false;
}

// ═══════════════════════════════════════════════════════════════
// Store 形状
// ═══════════════════════════════════════════════════════════════

export interface KnoweStore {
  // ── State ──
  convs: Record<string, Conv>;
  activeProjectId: string | null;
  activeView: string;
  cmdKOpen: boolean;
  conn: ConnStatus;
  notices: GlobalNotice[];
  projectOrder: string[];

  // ── [v0.44.8] 群聊列表右键菜单（后端持久态 + 本窗口展开态） ──
  /** project_id → pinned_at；键存在即置顶，时间戳越大越靠上。 */
  pinnedProjects: Record<string, number>;
  mutedProjects: Record<string, true>;
  foldedProjects: Record<string, true>;
  /** 3 个及以上置顶时，折叠标签的本窗口开合状态。 */
  pinnedCollapsed: boolean;
  /** 「折叠聊天」区域的本窗口开合状态。 */
  foldedOpen: boolean;
  conversationStatesLoaded: boolean;

  // ── Config (injectable for tests) ──
  agents: AgentRegistry;
  roleTypes: RoleType[];

  // ── Socket binding ──
  _socket: SocketAPI | null;

  /** [v1.0.19.4] 待发送附件（按会话暂存；归会话、发出即清、不落盘）。 */
  _attachments: Record<string, AttachmentInput[]>;

  // ── Actions: Conversation ──
  getConv: (projectId: string) => Conv;
  getActiveConv: () => Conv | null;
  switchProject: (projectId: string) => void;
  /**
   * [v0.37] 进入与某个群内成员的私聊。
   *
   *   `groupProjectId` = 这个人所在的群；`agentId` = 群里的谁（coordinator / Worker）。
   *   会话 id = `dm:{groupProjectId}:{agentId}`（一处约定，见 chat.ts）。
   *   本地先把私聊会话建出来（名字取该成员在群里的显示名），再切过去、要一次历史。
   *   —— 和 createProject 同一路数：先本地落地、视口立刻跟上，再让后端补历史。
   */
  enterDm: (groupProjectId: string, agentId: string) => void;
  /** [v0.37] 退出当前私聊，回到它所属的群聊（单击群头像/切走时用）。 */
  exitDm: (dmSessionId: string) => void;
  /** [v0.8c #1] 连上之后：把所有已知项目的快照都要一遍（排队，不打爆后端） */
  syncAllProjects: () => void;

  // ── [v0.8d #5] 未读 ──
  /** 窗口在不在前台。不在前台时，连「当前这个群」的新消息也算未读——你没在看。 */
  windowFocused: boolean;
  setWindowFocused: (focused: boolean) => void;
  /** 把某个会话标记为已读（点进去、或窗口重新获得焦点时） */
  markRead: (projectId: string) => void;
  registerProject: (projectId: string, projectName?: string) => Conv;
  getProjectList: () => { project_id: string; name: string }[];

  // ── [v0.44.8] 群聊列表菜单 ──
  syncConversationStates: () => Promise<void>;
  setProjectPinned: (projectId: string, enabled: boolean) => Promise<boolean>;
  setProjectMuted: (projectId: string, enabled: boolean) => Promise<boolean>;
  setProjectFolded: (projectId: string, enabled: boolean) => Promise<boolean>;
  renameProject: (projectId: string, projectName: string) => Promise<boolean>;
  /** 联系人视图：服务端提交成功后，从本窗口精确移除项目及其全部 DM 投影。 */
  deleteProjectPermanently: (projectId: string) => Promise<boolean>;
  /** 联系人视图：服务端提交成功后，只移除该 Agent 身份及其精确 DM 投影。 */
  deleteAgentPermanently: (projectId: string, agentId: string) => Promise<boolean>;
  togglePinnedCollapsed: () => void;
  toggleFoldedOpen: () => void;

  // ── Actions: Event handling ──
  handleEvent: (event: InboundEvent) => void;
  /**
   * [v1.0.23.6] 增量事件批量注入（HTTP 旁路预热通道专用）。
   * 与 handleEvent 的差异：不 bump 置顶、不算未读、不触发跳转——纯数据注入，
   * 快照到达后整体重建覆盖（快照永远赢）。seq 幂等由调用方（incrementalSync）
   * 按骨架 readSeq + socket 水位对齐保证。
   */
  applyIncrementalEvents: (projectId: string, events: InboundEvent[]) => void;
  ackTransientFrame: (projectId: string, frameId: string) => void;
  setConnStatus: (status: ConnStatus) => void;

  // ── Actions: Socket delegates ──
  sendMessage: (
    content: string, projectId?: string, clientMsgId?: string,
    opts?: {
      displayText?: string;
      quote?: MessageQuote;
      /** [v0.40.2 #4] 转发标记：气泡显示「转发自 X」并按 markdown 渲染正文。 */
      forwarded?: ForwardMeta;
      /** [v0.40.2 #4] 转发带来的文件（原格式随转发一起送达目标会话）。 */
      files?: ProducedFile[];
      /** [v1.0.19.4] 用户附件（路径+签名，无字节）——随消息出站，并挂到用户气泡上。 */
      attachments?: AttachmentInput[];
    },
  ) => string;
  approve: (approvalId: string, projectId: string) => void;
  reject: (approvalId: string, projectId: string) => void;
  /**
   * [v0.26] 在派活卡上提修改意见 —— 卡**原地**换一版指令，不落定、不发聊天消息。
   * 后端做完会重发一条同 card_id 的 approval_card，applyEvent 就地更新那一格。
   */
  feedbackInstruction: (approvalId: string, projectId: string, feedback: string) => void;
  /**
   * [v0.29 问题二] 让一个正在干活的成员放下手里的活。
   *
   * 和 approve / reject / feedbackInstruction 并列：**控制面**，直达引擎，
   * 不经聊天消息。用户按下这个按钮是**终局**，不是一条要项目经理去理解的提议。
   */
  stopWorker: (agentId: string) => void;
  /** [v0.7 A0] projectDir 是新建项目时用户选的目录（Worker 沙箱的根）
   *  [主动拉入worker] roles：建群时勾选的职能前缀（身份去重、数量不设限），建群后自动拉入。 */
  createProject: (
    projectId: string, projectName: string, projectDir?: string, approvalId?: string,
    roles?: string[],
  ) => void;
  /** [v1.0.23.4] 群聊中途添加 Agent 员工：roles 为职能前缀数组，可重复（同职能多选）。 */
  addAgents: (projectId: string, roles: string[]) => void;

  // ── Actions: 草稿（[v0.7 #1] 草稿归会话，不归输入框） ──
  setDraft: (projectId: string, text: string) => void;
  clearDraft: (projectId: string) => void;

  // ── Actions: 附件（[v1.0.19.4] 归会话、不落盘；发出即清） ──
  addAttachments: (projectId: string, picks: AttachmentInput[]) => void;
  removeAttachment: (projectId: string, path: string) => void;
  clearAttachments: (projectId: string) => void;

  // ── Actions: Optimistic rendering ──
  confirmEcho: (cmid: string) => void;
  suspectEcho: (cmid: string) => void;

  // ── Actions: Epoch reset ──
  clearProject: (projectId: string) => void;

  // ── Actions: Project auto-register ──
  ensureProject: (projectId: string, projectName?: string) => void;

  // ── Actions: Global notices ──
  addNotice: (message: string) => void;
  upsertNotice: (id: string, message: string) => void;

  // ── Actions: Lifecycle ──
  setSocket: (s: SocketAPI) => void;
  setView: (name: string) => void;
  toggleCmdK: () => void;
  closeCmdK: () => void;

  // ── [v0.40.0] 消息右键菜单：多选 / 引用 / 翻译 / 视图删除 / 全局 API Key ──

  /** 多选模式开没开（README §3.4）。开着时 .msgs 加 .selecting、Composer 半透明禁用。 */
  selecting: boolean;
  /** 选中的消息（键 = state.itemKeyOf）。Record 而非 Set：immer/序列化都省心。 */
  selectedKeys: Record<string, true>;
  /** 右键「引用」→ 输入框上方的引用条。null = 没有引用。 */
  quote: ChatQuote | null;

  /** 进入多选（可带第一条选中的消息）。 */
  enterSelect: (firstKey?: string) => void;
  /** 反选一条消息。 */
  toggleSelect: (key: string) => void;
  /** 退出多选：清空选中集、恢复输入框。 */
  exitSelect: () => void;

  setQuote: (q: ChatQuote) => void;
  clearQuote: () => void;

  /**
   * [v0.40.0] **仅从当前视图**移除这些气泡（右键「删除」/ 多选批量删除）。
   *
   * 不发任何 socket 命令、不动数据库——服务端的历史原封不动，
   * request_snapshot 重建后这些消息会回来。这正是 README 要的语义
   * （「仅从当前聊天界面移除此气泡，不从数据库删除」），也是诚实的语义：
   * 前端没有能力、也不假装有能力替所有人删除历史。
   */
  removeItemsFromView: (projectId: string, keys: string[]) => void;

  /**
   * [v0.40.1] 把若干「待转发内容」投进目标会话（转发弹窗点发送后调用）。
   *
   *   targetIds 里可以是项目 id（整群）或私聊 id `dm:{group}:{agent}`（群内某成员）——
   *   私聊目标若本地还没建过会话，这里按成员显示名补建（同 enterDm 的落地思路）。
   /**
    * [v1.0.23.1] ★ 转发 = 一条**正常的用户消息**（content = 用户配言），不是前端本地插一条。
    *   走 sendMessage 的正常路径（socket 出站 → 后端 → Agent）。LLM 模板由**后端构造**
    *   （用户{用户名}将{群名}中{来源者}的消息{原文}转发了过来，并配言{附言}）；
    *   前端只发结构化 forwarded 载荷，气泡显示配言 + 引用窗。
    */
   forwardMessages: (targetIds: string[], items: ForwardItem[], comment?: string) => void;
}

// ── [v0.40.2 #4] 转发内容结构化 ────────────────────────────────
//   转发 = 一条**正常的用户消息**（只是自动加了引用前缀），走跟手打消息完全一样的
//   发送路径（socket 出站 → 后端 → Agent），这样目标会话的 Agent 才会真的回复。

const _FWD_IMG = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const _FWD_VID = /\.(mp4|mov|webm|mkv|avi)$/i;

/** 文件在结构化正文里的占位（图片/视频/文件）。 */
function forwardFileMarker(f: ProducedFile): string {
  if (_FWD_IMG.test(f.name)) return i18n.t('favorites.01');
  if (_FWD_VID.test(f.name)) return i18n.t('favorites.02');
  return i18n.t('favorites.fileMarker', { name: f.name });
}

/** 被转发消息的「完整原文」：有文字用文字，纯文件用占位标注。 */
function forwardOriginal(fwd: ForwardItem): string {
  const t = (fwd.text || '').trim();
  if (t) return t;
  if (fwd.files && fwd.files.length) return fwd.files.map(forwardFileMarker).join(' ');
  return '';
}


// ═══════════════════════════════════════════════════════════════

const DEFAULT_PROJECT_ID = 'demo';

// ═══════════════════════════════════════════════════════════════
// Store 创建
// ═══════════════════════════════════════════════════════════════

export const useKnoweStore = create<KnoweStore>()(
  immer((set, get) => ({
    // ── State ──
    convs: {},
    activeProjectId: null,
    activeView: 'chats',
    cmdKOpen: false,
    conn: 'closed' as ConnStatus,
    notices: [],
    projectOrder: [],
    pinnedProjects: {},
    mutedProjects: {},
    foldedProjects: {},
    pinnedCollapsed: false,
    foldedOpen: false,
    conversationStatesLoaded: false,
    windowFocused: true,        // [v0.8d #5] 开机时窗口就在你眼前

    agents: DEFAULT_AGENTS,
    roleTypes: DEFAULT_ROLE_TYPES,
    _socket: null,
    _attachments: {},

    // ── [v0.40.0] 右键菜单一族的初始态 ──
    selecting: false,
    selectedKeys: {},
    quote: null,

    // ── Actions: Conversation ──

    getConv(projectId: string): Conv {
      const { convs } = get();
      return ensureConversation(convs, projectId);
    },

    getActiveConv(): Conv | null {
      const { activeProjectId, convs } = get();
      if (!activeProjectId) return null;
      return convs[activeProjectId] ?? null;
    },

    /**
     * [v0.8b #1] ★ 切群 = **把这个项目的历史要过来**。
     *
     *   原来这里只改了一个 `activeProjectId` 就完事：前端手上根本没有这个项目的
     *   任何事件——没有事件就没有 `registerMember`，于是花名册是空的、头像全是文字。
     *   后端 v0.8a-fix 已经把队伍在自己那边恢复好了（`wake_projects`），
     *   可它不会主动往一个**没开口要**的客户端推历史。缺的就是这一句「要」。
     *
     *   `request_snapshot` 只发一次：`_synced` 记着这条连接上已经同步过哪些项目。
     *   为什么要记：`state_snapshot` 会**清空并重建**这个会话的 items——
     *   每切一次就重建一次，屏幕会闪，而且正在飞的乐观气泡（还没等到回声的那条）
     *   会被一起抹掉。同步过一次就够了，之后靠增量事件跟。
     *
     *   连接一断，`_synced` 清空（见 setConnStatus）：新连接只重放**当前**那一个项目，
     *   别的项目又得重新要一遍。
     */
    switchProject(projectId: string): void {
      set((draft) => {
        draft.activeProjectId = projectId;
        // [v0.8d #5] 点进来了 = 看见了 → 红点清零
        ensureConversation(draft.convs, projectId).unread = 0;
        // [v1.0.24.7-P0-3] 引用条跨会话错发修复：quote 是全局字段，切群不清理会把
        //   A 群的消息引用带到 B 群发出去（气泡上的引用块指向 A 群消息）。
        //   切群时若 quote 不属于目标会话 → 就地清掉（治本；组件层 useEffect 校验为双保险）。
        if (draft.quote && draft.quote.projectId !== projectId) {
          draft.quote = null;
        }
      });

      // 用户正盯着这个群 → 插队，立刻发
      requestSnapshotOnce(projectId, () => get()._socket, true);
    },

    /**
     * [v0.37] 进入群内成员私聊。
     *
     *   会话 id = dm:{group}:{agent}。名字取该成员在群里的显示名（花名册已算好，
     *   跨重启稳定）——找不到就退回 agentId，绝不炸。建好本地会话后走 switchProject，
     *   它会置活、清红点、要一次快照（后端据 dm id 里的 projectId 路由到同一个引擎，
     *   把这段私聊历史投影回来）。
     */
    enterDm(groupProjectId: string, agentId: string): void {
      if (!groupProjectId || !agentId) return;
      const dmId = dmSessionId(groupProjectId, agentId);

      set((draft) => {
        // 名字、头像、父群归属都走同一条 DM 自愈路径；重复进入同一私聊也是幂等的。
        ensureConversation(draft.convs, dmId);
        // 私聊不进 projectOrder（它不是「项目」，左栏也不把它当项目列）——
        //   它只在「私聊面板模式」下由 ConvList 现算现画，退出即散。
      });

      // 复用 switchProject 的置活 + 清红点 + 要快照（一处逻辑，别抄第二遍）。
      get().switchProject(dmId);
    },

    /** [v0.37] 退出私聊 → 回到它所属的群聊。解析不出所属群 → 回知知。 */
    exitDm(sessionId: string): void {
      const group = dmGroupOf(sessionId);
      get().switchProject(group || PLATFORM_PROJECT_ID);
    },

    // ── [v0.8d #5] 未读 ──

    setWindowFocused(focused: boolean): void {
      set((draft) => {
        draft.windowFocused = focused;
        // 回到前台 → 你正看着的那个群，就算看过了
        if (focused && draft.activeProjectId) {
          ensureConversation(draft.convs, draft.activeProjectId).unread = 0;
        }
      });
    },

    markRead(projectId: string): void {
      if (!projectId) return;
      set((draft) => { ensureConversation(draft.convs, projectId).unread = 0; });
    },

    /**
     * [v0.8c #1] 把**所有**已知项目的快照都要一遍（连上之后跑一次）。
     *
     * 当前这个群优先（它在屏幕上），其余的排队慢慢来。
     * 已经要过的会被 _synced 挡掉，重复调用是安全的。
     */
    syncAllProjects(): void {
      const { activeProjectId, projectOrder } = get();
      const getSocket = (): SocketAPI | null => get()._socket;

      if (activeProjectId) requestSnapshotOnce(activeProjectId, getSocket, true);
      // 知知也要：她的窗口里有欢迎语和历史（后端 v0.8c 起会温载 __platform__）
      requestSnapshotOnce(PLATFORM_PROJECT_ID, getSocket);
      for (const pid of projectOrder) requestSnapshotOnce(pid, getSocket);
    },

    registerProject(projectId: string, projectName?: string): Conv {
      set((draft) => {
        const dm = parseDmId(projectId);
        if (dm) {
          const conv = ensureConversation(draft.convs, projectId);
          // 父群花名册尚未到达时，允许调用方提供一个非 raw 的临时显示名。
          if (projectName && projectName !== projectId && conv.projectName === dm.agentId) {
            conv.projectName = projectName;
          }
          draft.activeProjectId = projectId;
          normalizeProjectOrder(draft);
          return;
        }
        registerProject(draft.convs, projectId, projectName);
        applyProjectName(draft, projectId, projectName);
        if (!draft.projectOrder.includes(projectId)) insertProjectOrder(draft, projectId);
        draft.activeProjectId = projectId;
      });
      const { convs } = get();
      return convs[projectId] as Conv;
    },

    async syncConversationStates(): Promise<void> {
      if (_conversationSyncInFlight) {
        // 远端窗口的刷新信号若撞上旧 GET，不能被“已有请求”吞掉；在途请求结束后
        // 合并补跑一次，就能覆盖响应快照早于状态变更的竞态。
        _conversationSyncQueued = true;
        return _conversationSyncInFlight;
      }
      const task = (async (): Promise<void> => {
        try {
          const payload = await projectMenuJson<{ projects?: unknown[] }>('/projects/menu-state');
          const rows = Array.isArray(payload?.projects)
            ? payload.projects.map(parseConversationState).filter(
              (row): row is ProjectConversationState => !!row,
            )
            : [];
          set((draft) => {
            // GET 返回全量权威快照：先清旧键，服务端删掉/解除了的状态也能同步回来。
            draft.pinnedProjects = {};
            draft.mutedProjects = {};
            draft.foldedProjects = {};
            for (const row of rows) applyConversationState(draft, row);
            normalizeProjectOrder(draft);
            draft.conversationStatesLoaded = true;
          });
        } catch (error) {
          // 开机对账失败不能影响聊天主链；菜单动作自身会给用户明确失败提示。
          console.warn('[store] syncConversationStates failed', error);
        }
      })();
      _conversationSyncInFlight = task;
      try {
        await task;
      } finally {
        if (_conversationSyncInFlight === task) _conversationSyncInFlight = null;
        if (_conversationSyncQueued) {
          _conversationSyncQueued = false;
          scheduleConversationStateSync(0);
        }
      }
    },

    async setProjectPinned(projectId: string, enabled: boolean): Promise<boolean> {
      if (!projectId || projectId === PLATFORM_PROJECT_ID || !!parseDmId(projectId)) return false;
      const previous = snapshotConversationState(get(), projectId);
      const optimistic: ProjectConversationState = {
        ...previous,
        pinned: enabled,
        folded: enabled ? false : previous.folded,
        pinned_at: enabled ? Date.now() * 1000 : 0,
      };
      set((draft) => { applyConversationState(draft, optimistic); });
      try {
        const raw = await projectMenuJson<unknown>('/projects/pin', {
          method: 'POST', body: JSON.stringify({ project_id: projectId, enabled }),
        });
        const row = parseConversationState(raw);
        if (!row) throw new Error(i18n.t('store.14'));
        set((draft) => { applyConversationState(draft, row); });
        return true;
      } catch (error) {
        set((draft) => { applyConversationState(draft, previous); });
        const message = error instanceof Error ? error.message : i18n.t('store.20');
        get().addNotice(message);
        scheduleConversationStateSync(0);
        return false;
      }
    },

    async setProjectMuted(projectId: string, enabled: boolean): Promise<boolean> {
      if (!projectId || projectId === PLATFORM_PROJECT_ID || !!parseDmId(projectId)) return false;
      const previous = snapshotConversationState(get(), projectId);
      const optimistic: ProjectConversationState = { ...previous, muted: enabled };
      set((draft) => { applyConversationState(draft, optimistic); });
      try {
        const raw = await projectMenuJson<unknown>('/projects/mute', {
          method: 'POST', body: JSON.stringify({ project_id: projectId, enabled }),
        });
        const row = parseConversationState(raw);
        if (!row) throw new Error(i18n.t('store.12'));
        set((draft) => { applyConversationState(draft, row); });
        return true;
      } catch (error) {
        set((draft) => { applyConversationState(draft, previous); });
        const message = error instanceof Error ? error.message : i18n.t('store.04');
        get().addNotice(message);
        scheduleConversationStateSync(0);
        return false;
      }
    },

    async setProjectFolded(projectId: string, enabled: boolean): Promise<boolean> {
      if (!projectId || projectId === PLATFORM_PROJECT_ID || !!parseDmId(projectId)) return false;
      const previous = snapshotConversationState(get(), projectId);
      const optimistic: ProjectConversationState = {
        ...previous,
        folded: enabled,
        pinned: enabled ? false : previous.pinned,
        pinned_at: enabled ? 0 : previous.pinned_at,
      };
      set((draft) => { applyConversationState(draft, optimistic); });
      try {
        const raw = await projectMenuJson<unknown>('/projects/fold', {
          method: 'POST', body: JSON.stringify({ project_id: projectId, enabled }),
        });
        const row = parseConversationState(raw);
        if (!row) throw new Error(i18n.t('store.13'));
        set((draft) => { applyConversationState(draft, row); });
        return true;
      } catch (error) {
        set((draft) => { applyConversationState(draft, previous); });
        const message = error instanceof Error ? error.message : i18n.t('store.08');
        get().addNotice(message);
        scheduleConversationStateSync(0);
        return false;
      }
    },

    async renameProject(projectId: string, projectName: string): Promise<boolean> {
      if (!projectId || projectId === PLATFORM_PROJECT_ID || !!parseDmId(projectId)) return false;
      const name = projectName.trim();
      if (!name) return false;
      try {
        const raw = await projectMenuJson<unknown>('/projects/rename', {
          method: 'POST', body: JSON.stringify({ project_id: projectId, project_name: name }),
        });
        if (!raw || typeof raw !== 'object'
            || typeof (raw as Record<string, unknown>).project_name !== 'string') {
          throw new Error(i18n.t('store.15'));
        }
        const canonicalName = String((raw as Record<string, unknown>).project_name);
        set((draft) => { applyProjectName(draft, projectId, canonicalName); });
        return true;
      } catch (error) {
        const message = error instanceof Error ? error.message : i18n.t('store.24');
        get().addNotice(message);
        // 网络/广播可能恰好断在后端提交之后；用权威快照对账，避免“其实改成了但本窗还旧名”。
        scheduleConversationStateSync(0);
        return false;
      }
    },

    async deleteProjectPermanently(projectId: string): Promise<boolean> {
      if (!projectId || projectId === PLATFORM_PROJECT_ID || !!parseDmId(projectId)) return false;
      const operationId = deleteOperationId();
      beginDeleteProgress(operationId, projectId);
      try {
        const raw = await projectMenuJson<unknown>('/projects/permanent-delete', {
          method: 'POST', body: JSON.stringify({ project_id: projectId, operation_id: operationId }),
        });
        const result = raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
        const canonicalProjectId = result && typeof result.project_id === 'string'
          ? result.project_id : '';
        const requestProjectId = result && typeof result.request_project_id === 'string'
          ? result.request_project_id : '';
        if (!result || result.deleted !== true || !canonicalProjectId
            || (canonicalProjectId !== projectId && requestProjectId !== projectId)) {
          throw new Error(i18n.t('store.11'));
        }

        // The card can still carry an old optimistic/request id while the server has
        // already canonicalised it.  Remove both exact ids; DM ownership is parsed,
        // never prefix-matched, so deleting project p cannot touch project p_x.
        const deletedProjectIds = new Set<string>([projectId, canonicalProjectId]);

        const belongsToDeletedProject = (conversationId: string): boolean => {
          if (deletedProjectIds.has(conversationId)) return true;
          const parentId = parseDmId(conversationId)?.projectId;
          return !!parentId && deletedProjectIds.has(parentId);
        };
        const doomedIds = new Set<string>(deletedProjectIds);
        for (const convId of Object.keys(get().convs)) {
          if (belongsToDeletedProject(convId)) doomedIds.add(convId);
        }
        // _synced 可能含尚未进入 convs 的预取 DM；按 parseDmId 精确归属清理，
        // 不用字符串前缀（项目 p 不能误伤 p_x）。排队中的快照也一并撤销。
        for (const convId of Array.from(_synced)) {
          if (belongsToDeletedProject(convId)) _synced.delete(convId);
        }
        for (let i = _snapQueue.length - 1; i >= 0; i -= 1) {
          if (belongsToDeletedProject(_snapQueue[i])) _snapQueue.splice(i, 1);
        }

        set((draft) => {
          for (const convId of doomedIds) delete draft.convs[convId];
          draft.projectOrder = draft.projectOrder.filter((id) => !deletedProjectIds.has(id));
          for (const deletedId of deletedProjectIds) {
            delete draft.pinnedProjects[deletedId];
            delete draft.mutedProjects[deletedId];
            delete draft.foldedProjects[deletedId];
          }

          if (draft.quote && belongsToDeletedProject(draft.quote.projectId)) draft.quote = null;

          if (draft.activeProjectId && belongsToDeletedProject(draft.activeProjectId)) {
            draft.activeProjectId = draft.convs[PLATFORM_PROJECT_ID]
              ? PLATFORM_PROJECT_ID
              : (draft.projectOrder[0] || null);
            draft.selecting = false;
            draft.selectedKeys = {};
          }
          if (Object.keys(draft.pinnedProjects).length < 3) draft.pinnedCollapsed = false;
          if (Object.keys(draft.foldedProjects).length === 0) draft.foldedOpen = false;
          normalizeProjectOrder(draft);
        });

        const warnings = Array.isArray(result.warnings)
          ? result.warnings.filter((item): item is string => typeof item === 'string' && !!item.trim())
          : [];
        if (warnings.length) {
          finishDeleteProgress(operationId, i18n.t('store.deleteWarnings', { warnings: warnings.join('；') }));
        } else {
          finishDeleteProgress(
            operationId,
            result.cleanup_pending === true ? i18n.t('store.22') : i18n.t('store.21'),
          );
        }
        return true;
      } catch (error) {
        const message = error instanceof Error ? error.message : i18n.t('store.23');
        const updated = finishDeleteProgress(operationId, i18n.t('store.deleteFailed', { message }));
        if (!updated) get().addNotice(message);
        return false;
      }
    },

    async deleteAgentPermanently(projectId: string, agentId: string): Promise<boolean> {
      if (!projectId || projectId === PLATFORM_PROJECT_ID || !!parseDmId(projectId) || !agentId) {
        return false;
      }
      try {
        const raw = await projectMenuJson<unknown>('/agents/permanent-delete', {
          method: 'POST', body: JSON.stringify({ project_id: projectId, agent_id: agentId }),
        });
        if (!raw || typeof raw !== 'object'
            || (raw as Record<string, unknown>).deleted !== true
            || (raw as Record<string, unknown>).project_id !== projectId
            || (raw as Record<string, unknown>).agent_id !== agentId) {
          throw new Error(i18n.t('store.10'));
        }

        const dmId = dmSessionId(projectId, agentId);
        _synced.delete(dmId);
        for (let i = _snapQueue.length - 1; i >= 0; i -= 1) {
          if (_snapQueue[i] === dmId) _snapQueue.splice(i, 1);
        }
        set((draft) => {
          const group = draft.convs[projectId];
          if (group) group.members = group.members.filter((member) => member.id !== agentId);
          delete draft.convs[dmId];

          if (draft.quote?.projectId === dmId) draft.quote = null;
          if (draft.activeProjectId === dmId) {
            draft.activeProjectId = draft.convs[projectId]
              ? projectId
              : (draft.convs[PLATFORM_PROJECT_ID] ? PLATFORM_PROJECT_ID : null);
            draft.selecting = false;
            draft.selectedKeys = {};
          }
        });
        return true;
      } catch (error) {
        const message = error instanceof Error ? error.message : i18n.t('store.02');
        get().addNotice(message);
        return false;
      }
    },

    togglePinnedCollapsed(): void {
      set((draft) => {
        const count = Object.keys(draft.pinnedProjects).length;
        draft.pinnedCollapsed = count >= 3 ? !draft.pinnedCollapsed : false;
      });
    },

    toggleFoldedOpen(): void {
      set((draft) => {
        draft.foldedOpen = Object.keys(draft.foldedProjects).length
          ? !draft.foldedOpen : false;
      });
    },

    getProjectList() {
      const { convs } = get();
      return getProjectList(convs).filter((row) => !parseDmId(row.project_id));
    },

    // ── Actions: Event handling ──

    /**
     * [v1.0.24.4] 权威活动账本校准入口（replay_complete 转交）。
     * 会话不存在则跳过——project_created 到位后，快照自带的账本会再做一次校准。
     */
    calibrateActivity(projectId: string, activity: ActivityLedgerEntry[]): void {
      set((draft) => {
        const c = draft.convs[projectId];
        if (!c) return;
        calibrateRosterActivity(c, activity);
      });
    },

    handleEvent(event: InboundEvent): void {
      const { agents, roleTypes } = get();
      const rawEvent = event as unknown as Record<string, unknown>;
      const eventType = String(rawEvent.type || '');
      const pid = rawEvent.project_id as string || DEFAULT_PROJECT_ID;
      const requestProjectId = eventType === 'project_created'
        ? (event as InboundEvent & { request_project_id?: string }).request_project_id
        : undefined;
      if (requestProjectId && requestProjectId !== pid) {
        reconcileSnapshotAlias(requestProjectId, pid);
      }

      set((draft) => {
        if (eventType === 'project_delete_progress') {
          const operationId = typeof rawEvent.operation_id === 'string' ? rawEvent.operation_id : '';
          const message = typeof rawEvent.message === 'string' ? rawEvent.message : '';
          const elapsedMs = typeof rawEvent.elapsed_ms === 'number' ? rawEvent.elapsed_ms : 0;
          if (!operationId || !message) return;
          const current = _deleteProgress.get(operationId);
          if (!current) return;
          if (current) current.message = message;
          if (current.shown || elapsedMs >= 2000) {
            current.shown = true;
            const existing = draft.notices.find((notice) => notice.id === operationId);
            const timestamp = new Date().toISOString();
            if (existing) {
              existing.message = message;
              existing.timestamp = timestamp;
            } else {
              draft.notices.push({ id: operationId, message, timestamp });
            }
          }
          return;
        }

        // 新事件先在 store 入口消费；旧 transport 若尚未登记它们，后端会补 project_created
        // 触发同一份 HTTP 全量对账，所以多窗口和滚动升级都不会出现两套状态。
        if (eventType === 'project_state_changed') {
          const row = parseConversationState(rawEvent);
          if (row) applyConversationState(draft, row);
          return;
        }
        if (eventType === 'project_renamed') {
          const name = typeof rawEvent.project_name === 'string' ? rawEvent.project_name : undefined;
          applyProjectName(draft, pid, name);
          return;
        }

        if (eventType === 'project_directory_restored') {
          // [v1.0.21.2] 目录恢复成功 → 同步 conv.projectDir（事件带新路径）。
          // 此前该事件只喂给目录 store 关卡片，主 store 不消费 → 资料卡片
          // 显示旧路径、文件夹 icon 打开失败。这里用事件里的 project_dir 更新。
          const dir = typeof rawEvent.project_dir === 'string' ? rawEvent.project_dir : undefined;
          if (dir) {
            const conv = draft.convs[pid];
            if (conv) conv.projectDir = dir;
          }
          return;
        }

        if (eventType === 'project_created') {
          const pce = event as InboundEvent & {
            type: 'project_created';
            request_project_id?: string;
            // [v0.9c] name 也随握手一起来了（「前端 1」）—— 见后端 server._members_of
            members?: { id: string; role: string; name?: string }[];
          };
          const dm = parseDmId(pce.project_id);
          if (dm) {
            // 旧后端会把运行期 DM Hub 频道混进“项目列表”，并补发
            // project_created(project_name='dm:...')。DM 只补齐本地会话，绝不进群排序/菜单状态。
            const conv = ensureConversation(draft.convs, pce.project_id);
            if (pce.project_name && pce.project_name !== pce.project_id
                && conv.projectName === dm.agentId) {
              conv.projectName = pce.project_name;
            }
            let index = draft.projectOrder.indexOf(pce.project_id);
            while (index >= 0) {
              draft.projectOrder.splice(index, 1);
              index = draft.projectOrder.indexOf(pce.project_id);
            }
            delete draft.pinnedProjects[pce.project_id];
            delete draft.mutedProjects[pce.project_id];
            delete draft.foldedProjects[pce.project_id];
            normalizeProjectOrder(draft);
            return;
          }
          if (pce.request_project_id && pce.request_project_id !== pce.project_id) {
            reconcileProjectAlias(draft, pce.request_project_id, pce.project_id);
            reconcileConversationStateAlias(draft, pce.request_project_id, pce.project_id);
          }
          // project_created 也被 v0.44.8 用作旧传输层可识别的“刷新信号”。只有此前
          // 真不存在的项目才属于“刚建群”，状态对账/改名补发绝不能把别的窗口强行切群。
          const existedBefore = !!draft.convs[pce.project_id]
            || draft.projectOrder.includes(pce.project_id);
          const projDir = typeof (pce as Record<string, unknown>).project_dir === 'string'
            ? (pce as Record<string, unknown>).project_dir as string
            : undefined;
          const conv = registerProject(draft.convs, pce.project_id, pce.project_name, projDir);
          applyProjectName(draft, pce.project_id, pce.project_name);
          if (draft.mutedProjects[pce.project_id] || draft.foldedProjects[pce.project_id]) {
            conv.unread = 0;
          }

          /*
           * [v0.8d #1] ★ 花名册**随握手一起到**——头像在第一帧就是对的。
           *
           *   以前要知道群里有谁，得先把整条会话的快照要过来（v0.8c 的排队机制），
           *   20 个群就是 20 次往返，头像一个一个往外蹦，蹦三秒。
           *   而后端一直知道这些人是谁（wake_projects 早把花名册温载进内存了），
           *   只是没有一个字段可以说。现在它捎在 project_created 里一起来了。
           *
           *   注意：**只登记人，不往消息流里塞系统行**。
           *   「XX 已加入项目」是当年真的发生过的一件事，它在历史里，
           *   不该因为你今天开了一次软件就重演一遍。
           */
          for (const m of pce.members ?? []) {
            // [v0.9c] ★ 名字从花名册来 —— 这就是「重启之后名字不变」的那一环。
            //   前端不再自己掷骰子（那是「昨天叫林知远、今天叫陈思涵」的根）。
            // [v0.9d] 第八个参数 active=true：这份名单**就是后端此刻的花名册**，
            //   名单里的人一律算在队（归档的人后端根本不会发过来）。
            registerMember(conv, m.id, agents, roleTypes, m.role, m.name, true);
          }
          // 若 DM 事件比所属群握手更早到，它只能先退回 agentId；花名册现在齐了，
          // 立即把这些既有私聊的名字/头像升级成群内权威显示值。
          for (const sessionId of Object.keys(draft.convs)) {
            const existingDm = parseDmId(sessionId);
            if (existingDm?.projectId === pce.project_id) {
              ensureConversation(draft.convs, sessionId);
            }
          }

          if (!draft.projectOrder.includes(pce.project_id)) {
            // 新项目进入普通区最上方；若服务端对账表明它已置顶/折叠，normalize 会归位。
            insertProjectOrder(draft, pce.project_id);
          } else {
            normalizeProjectOrder(draft);
          }
          /*
           * [v0.4] 追加一条规则（不是重写）：
           *   如果用户此刻正在跟知知说话，而一个新项目刚被建出来——那多半就是
           *   知知刚给他建的。这时候自动切过去，别让他自己再去左栏点一下。
           *   （知知的会话 __platform__ 本身不是项目，不会走到这里。）
           */
          /*
           * ★ [v0.8d] 只在**连上之后**才自动跳。
           *
           *   握手期间（conn = 'handshaking'）服务端会把**每一个**已有项目都补发一条
           *   project_created —— 上面这条规则会让 activeProjectId 被它们挨个改写一遍，
           *   最后停在「最后一个被补发的项目」上。开机本该停在知知那儿，
           *   结果人被甩进了某个群，而且是哪个群全看字典序。
           *
           *   「知知刚给你建了个群 → 自动跳进去」这条规则本身是对的，
           *   它只是不该在开机补发历史的时候生效。加一道闸：conn 必须已经是 live。
           */
          if (!existedBefore && draft.conn === 'live'
              && (!draft.activeProjectId || draft.activeProjectId === PLATFORM_PROJECT_ID)) {
            draft.activeProjectId = pce.project_id;
            getConv(draft.convs, pce.project_id).unread = 0;
          }
          return;
        }

        // 服务器级 error（无 project_id）→ 进 notices，不进会话
        if (event.type === 'error' && !(event as Record<string, unknown>).project_id) {
          draft.notices.push({
            // [v0.40.2 #1] 服务器级的 LLM 报错同样人性化：余额不足 → 中文提示。
            message: humanizeLlmError(event.message),
            timestamp: new Date().toISOString(),
          });
          return;
        }

        const conv = ensureConversation(draft.convs, pid);

        // ★ 顺序：先问「这条事件算不算有动静」（stream_delta 要看**改之前**的气泡状态），
        //   再 applyEvent，最后才置顶。
        const bump = isBumpEvent(conv, event);
        applyEvent(conv, event, agents, roleTypes);
        if (bump) bumpProjectOrder(draft, pid);

        /*
         * [v0.8d #5] 未读。
         *
         *   什么叫「没看见」：这条消息来的时候，**它不在你眼前**——
         *   要么你在别的群里，要么整个窗口都不在前台（你去看别的应用了）。
         *   后一种情况常被漏掉：人切出去泡了杯咖啡，回来时当前群多了十条消息，
         *   左栏却一声不吭。窗口失焦时，「当前群」也不算在看。
         */
        const watching = pid === draft.activeProjectId && draft.windowFocused;
        const silent = !!draft.mutedProjects[pid] || !!draft.foldedProjects[pid];
        if (silent) {
          // muted/folded：不论成员状态还是群消息，都不能留下红点或任务栏数字。
          conv.unread = 0;
        } else if (!watching && isUnreadEvent(event)) {
          conv.unread = (conv.unread || 0) + 1;
        }
      });

      const projected = get().convs[pid];
      if (projected) {
        scheduleTransientFrameFallback(pid, pendingTransientFrameIds(projected));
      }

      if (eventType === 'project_created' && !parseDmId(pid)) scheduleConversationStateSync();
    },

    /**
     * [v1.0.23.6] 增量事件批量注入（HTTP 旁路预热通道专用）。
     * 纯数据注入：不 bump 置顶、不算未读、不触发跳转——快照到达后整体重建覆盖。
     */
    applyIncrementalEvents(projectId: string, events: InboundEvent[]): void {
      if (!projectId || !Array.isArray(events) || events.length === 0) return;
      const { agents, roleTypes } = get();
      set((draft) => {
        const conv = ensureConversation(draft.convs, projectId);
        for (const ev of events) {
          applyEvent(conv, ev, agents, roleTypes);
        }
      });
      scheduleTransientFrameFallback(projectId, pendingTransientFrameIds(get().convs[projectId]));
    },

    ackTransientFrame(projectId: string, frameId: string): void {
      set((draft) => {
        const conv = draft.convs[projectId];
        if (conv) acknowledgeTransientFrame(conv, frameId);
      });
    },

    setConnStatus(status: ConnStatus): void {
      // [v0.8b #1] 连接重新握手了 → 之前「要过历史」的记账全部作废。
      //   新连接只重放**当前**那一个项目（replay_request 只带一个 project_id），
      //   别的项目手上那份可能已经过期了，下次切进去得重新要一遍。
      if (status === 'connecting' || status === 'handshaking') resetSynced();
      set((draft) => { draft.conn = status; });

      /*
       * [v0.8c #1] ★ 连上了 → 把所有项目的花名册都要回来。
       *
       *   延迟 250ms 再跑：replay_complete 之后，project_created 可能还在路上
       *   （每客户端补发一遍）。等它们都落地，projectOrder 才是齐的——
       *   否则刚开机那一瞬间列表里只有一两个项目，剩下的还是得靠点。
       *
       *   每条连接只排一次（_bulkScheduled）；快照本身也只要一次（_synced）。
       */
      if (status === 'live') {
        scheduleConversationStateSync(0);
        if (!_bulkScheduled) {
          _bulkScheduled = true;
          setTimeout(() => { get().syncAllProjects(); }, 250);
        }
      }
    },

    // ── Actions: Socket delegates ──

    sendMessage(
      content: string, projectId?: string, clientMsgId?: string,
      opts?: {
        displayText?: string; quote?: MessageQuote;
        forwarded?: ForwardMeta; files?: ProducedFile[];
        attachments?: AttachmentInput[];
      },
    ): string {
      const { _socket, activeProjectId } = get();
      const pid = projectId || activeProjectId || DEFAULT_PROJECT_ID;
      if (!_socket) {
        console.warn('[store] sendMessage: no socket');
        return '';
      }
      const cmid = _socket.sendMessage(content, pid, clientMsgId, opts?.attachments, opts?.forwarded);

      // ★ 乐观渲染：立即插入 pending 气泡
      if (cmid) {
        set((draft) => {
          const conv = ensureConversation(draft.convs, pid);
          conv.items.push({
            kind: 'user',
            // [v0.40.1] 发给后端的是 content（可能含结构化引用/转发前缀）；气泡上显示的是
            //   displayText（用户实际打的字 / 转发原文）。两者分开——引用/转发要在气泡上呈现，
            //   而不是把结构化串塞进正文。
            text: opts?.displayText ?? content,
            cmid,
            delivery: 'pending',
            ...(opts?.quote ? { quote: opts.quote } : {}),
            // [v0.40.2 #4] 转发：气泡带「转发自 X」标记 + 原格式文件（走既有 MessageBubble 渲染）。
            ...(opts?.forwarded ? { forwarded: opts.forwarded } : {}),
            ...(opts?.files && opts.files.length ? { files: opts.files } : {}),
            ...(opts?.attachments && opts.attachments.length ? { attachments: opts.attachments } : {}),
          });
          // [v0.7b #2] 也乐观置顶：自己刚说完话，这个群就该在最上面——
          //   不必等服务端的 user_echo 绕一圈回来（断线时它根本不会回来）。
          bumpProjectOrder(draft, pid);
        });
      }

      return cmid;
    },

    approve(approvalId: string, projectId: string): void {
      const pid = projectId || get().activeProjectId || DEFAULT_PROJECT_ID;
      get()._socket?.approve(approvalId, pid);
    },

    reject(approvalId: string, projectId: string): void {
      const pid = projectId || get().activeProjectId || DEFAULT_PROJECT_ID;
      get()._socket?.reject(approvalId, pid);
    },

    feedbackInstruction(approvalId: string, projectId: string, feedback: string): void {
      const pid = projectId || get().activeProjectId || DEFAULT_PROJECT_ID;
      get()._socket?.feedbackInstruction(approvalId, pid, feedback);
    },

    stopWorker(agentId: string): void {
      const projectId = get().activeProjectId;
      if (!projectId || !agentId) return;
      // 二次确认在 RosterPanel 里已经拦过一道了（v0.29 问题二 · 验收 8）。
      // 一条 stop_worker 出站 = 用户已经点过两次。
      get()._socket?.stopWorker(projectId, agentId);
    },
    /**
     * [v0.7 #5] 建群之后视口要**立刻**跳到新群。
     *
     *   原来这里只发了一条 socket 指令就完事：本地什么也没建，activeProjectId 没动，
     *   所以点完「创建」，人还站在原来的会话里，新群要等后端 project_created 回来才出现——
     *   而 handleEvent 里的自动跳转有个前提（「当前正在跟知知说话」），
     *   从别的群里点加号建群，这个前提不成立，于是**不跳**。
     *
     *   现在先在本地把项目注册出来、置顶、切过去，再发指令。后端回来的 project_created
     *   会落到同一个 project_id 上（registerProject 是幂等的），不会建出第二个。
     *
     * [v0.7 A0] projectDir：用户在弹窗里选的目录，随指令一起发给后端当 workspace_root。
     */
    createProject(
      projectId: string, projectName: string, projectDir?: string, approvalId?: string,
      roles?: string[],
    ): void {
      set((draft) => {
        registerProject(draft.convs, projectId, projectName, projectDir);
        applyProjectName(draft, projectId, projectName);
        if (!draft.projectOrder.includes(projectId)) insertProjectOrder(draft, projectId);
        draft.activeProjectId = projectId;     // ★ 就是这一行让视口跟着走
      });

      const socket = get()._socket;
      if (!socket) {
        console.warn('[store] createProject: no socket');
        return;
      }

      const send = socket.createProject as CreateProjectFn;
      /*
       * [v0.7b #1] ★ 「项目目录不生效」十有八九就是死在这一行上。
       *
       *   socket.ts 在禁改清单里，v0.7 只留了一句 README 让人手工接第三参。
       *   要是没接，`project_dir` 会在传输层被**默默丢掉**：前端显示选好了，
       *   后端从头到尾没收到过这个字段，于是老老实实用默认目录——
       *   而屏幕上没有任何一处提示，谁也查不出来。
       *
       *   所以这里不只是 console.warn 了（控制台没人天天看着）：往全局通知里塞一条，
       *   **顶到界面上**。响亮失败，别悄悄失败。
       */
      if (projectDir && send.length < 3) {
        const msg = i18n.t('store.03')
          + i18n.t('store.25');
        console.warn('[store] ' + msg);
        set((draft) => {
          draft.notices.push({ message: msg, timestamp: new Date().toISOString() });
        });
      }
      send(projectId, projectName, projectDir, approvalId, roles);
    },

    /**
     * [v1.0.23.4] 群聊中途添加 Agent 员工：按钮直达，不经审批卡。
     * roles 可含重复（同职能多选），后端自动编号 fe_1 占用 → fe_2/fe_3…。
     */
    addAgents(projectId: string, roles: string[]): void {
      if (!projectId || !roles || roles.length === 0) return;
      const socket = get()._socket;
      if (!socket) {
        console.warn('[store] addAgents: no socket');
        return;
      }
      socket.addAgents(projectId, roles);
    },

    // ── Actions: 草稿 ──

    setDraft(projectId: string, text: string): void {
      if (!projectId) return;
      set((draft) => { ensureConversation(draft.convs, projectId).draft = text; });
    },

    clearDraft(projectId: string): void {
      if (!projectId) return;
      set((draft) => { ensureConversation(draft.convs, projectId).draft = ''; });
    },

    // ── Actions: 附件 [v1.0.19.4] ──
    addAttachments(projectId: string, picks: AttachmentInput[]): void {
      if (!projectId || !picks || picks.length === 0) return;
      set((draft) => {
        const cur = draft._attachments[projectId] ?? [];
        const seen = new Set(cur.map((p) => p.path));
        const merged = [...cur];
        for (const p of picks) {
          if (p && p.path && !seen.has(p.path)) { merged.push(p); seen.add(p.path); }
        }
        draft._attachments[projectId] = merged;
      });
    },

    removeAttachment(projectId: string, path: string): void {
      if (!projectId) return;
      set((draft) => {
        const cur = draft._attachments[projectId];
        if (!cur) return;
        const next = cur.filter((p) => p.path !== path);
        if (next.length) draft._attachments[projectId] = next;
        else delete draft._attachments[projectId];
      });
    },

    clearAttachments(projectId: string): void {
      if (!projectId) return;
      set((draft) => { delete draft._attachments[projectId]; });
    },

    // ── Actions: Optimistic rendering ──

    confirmEcho(cmid: string): void {
      set((draft) => {
        for (const pid of Object.keys(draft.convs)) {
          const conv = draft.convs[pid];
          if (!conv?.items) continue;
          // [v0.3-UI 编译修复] 谓词改为类型守卫（TS2339：AgentItem 上没有 delivery）
          const item = conv.items.find(
            (it): it is UserItem => it.kind === 'user' && it.cmid === cmid && it.delivery === 'pending',
          );
          if (item) {
            item.delivery = 'confirmed';
            return;
          }
        }
      });
    },

    suspectEcho(cmid: string): void {
      set((draft) => {
        for (const pid of Object.keys(draft.convs)) {
          const conv = draft.convs[pid];
          if (!conv?.items) continue;
          // [v0.3-UI 编译修复] 谓词改为类型守卫（TS2339）
          const item = conv.items.find(
            (it): it is UserItem => it.kind === 'user' && it.cmid === cmid,
          );
          if (item) {
            item.delivery = 'suspect';
            return;
          }
        }
      });
    },

    // ── Actions: Epoch reset ──

    clearProject(projectId: string): void {
      // [v0.8b #1] 纪元重置 = 服务端换了一茬（重启过）。这个项目手上的历史清空了，
      //   「已经同步过」的记账当然也不算数——transport 会紧接着自己发一次
      //   request_snapshot，但万一它没发，切进去时我们还能再要一次。
      _synced.delete(projectId);
      set((draft) => {
        const conv = draft.convs[projectId];
        if (conv) {
          conv.items = [];
          conv.members = [];
          conv.banner = null;
        }
      });
    },

    ensureProject(projectId: string, projectName?: string): void {
      const dm = parseDmId(projectId);
      // [v0.8c #1] 连上之后才冒出来的真实项目：给它要一份快照。旧后端误补发的
      // dm:* 不是项目，既不进列表，也不在用户未打开时批量拉取私聊历史。
      if (get().conn === 'live' && !dm) {
        requestSnapshotOnce(projectId, () => get()._socket);
      }

      set((draft) => {
        if (dm) {
          const conv = ensureConversation(draft.convs, projectId);
          if (projectName && projectName !== projectId && conv.projectName === dm.agentId) {
            conv.projectName = projectName;
          }
          normalizeProjectOrder(draft);
          return;
        }
        if (!draft.convs[projectId]) {
          registerProject(draft.convs, projectId, projectName || projectId);
        }
        applyProjectName(draft, projectId, projectName);
        if (!draft.projectOrder.includes(projectId)) insertProjectOrder(draft, projectId);
        if (!draft.activeProjectId) draft.activeProjectId = projectId;
      });
    },

    // ── Actions: Global notices ──

    addNotice(message: string): void {
      set((draft) => {
        draft.notices.push({
          message,
          timestamp: new Date().toISOString(),
        });
      });
    },

    upsertNotice(id: string, message: string): void {
      set((draft) => {
        const timestamp = new Date().toISOString();
        const existing = draft.notices.find((notice) => notice.id === id);
        if (existing) {
          existing.message = message;
          existing.timestamp = timestamp;
        } else {
          draft.notices.push({ id, message, timestamp });
        }
      });
    },

    // ── Actions: Lifecycle ──

    setSocket(s: SocketAPI): void {
      set((draft) => { draft._socket = s; });
    },

    setView(name: string): void {
      set((draft) => { draft.activeView = name; });
    },

    toggleCmdK(): void {
      set((draft) => { draft.cmdKOpen = !draft.cmdKOpen; });
    },

    closeCmdK(): void {
      set((draft) => { draft.cmdKOpen = false; });
    },

    // ── [v0.40.0] 多选 ──

    enterSelect(firstKey?: string): void {
      set((draft) => {
        draft.selecting = true;
        draft.selectedKeys = firstKey ? { [firstKey]: true } : {};
      });
    },

    toggleSelect(key: string): void {
      if (!key) return;
      set((draft) => {
        if (!draft.selecting) return;
        if (draft.selectedKeys[key]) delete draft.selectedKeys[key];
        else draft.selectedKeys[key] = true;
      });
    },

    exitSelect(): void {
      set((draft) => {
        draft.selecting = false;
        draft.selectedKeys = {};
      });
    },

    // ── [v0.40.0] 引用 ──

    setQuote(q: ChatQuote): void {
      set((draft) => { draft.quote = q; });
    },

    clearQuote(): void {
      set((draft) => { draft.quote = null; });
    },

    // ── [v0.40.0] 视图删除 ──

    removeItemsFromView(projectId: string, keys: string[]): void {
      if (!projectId || keys.length === 0) return;
      set((draft) => {
        const conv = draft.convs[projectId];
        if (!conv) return;
        const drop = new Set(keys);
        // 键在渲染时按「当时的 items + 下标」算出——这里用同一把 itemKeyOf 现算现配。
        conv.items = conv.items.filter((it, i) => !drop.has(itemKeyOf(it, i)));
      });
    },

    // ── [v0.40.1] 转发 ──

    forwardMessages(targetIds: string[], items: ForwardItem[], comment?: string): void {
      if (targetIds.length === 0 || items.length === 0) return;

      for (const targetId of targetIds) {
        const targetDm = parseDmId(targetId);
        // 私聊目标（dm:group:agent）统一走自愈入口：即便旧会话已存在但名字是 raw id，
        // 转发前也会按群花名册修正。普通会话仍只在缺失时补建。
        if (targetDm || !get().convs[targetId]) {
          let name = targetId;
          let parentId: string | undefined;
          let parentName: string | undefined;
          if (targetDm) {
            const groupConv = get().convs[targetDm.projectId];
            const member = groupConv?.members?.find((m) => m.id === targetDm.agentId);
            name = member?.display.name || targetDm.agentId;
            parentId = targetDm.projectId;
            parentName = groupConv?.projectName || targetDm.projectId;
          }
          set((draft) => {
            const c = targetDm
              ? ensureConversation(draft.convs, targetId)
              : registerProject(draft.convs, targetId, name);
            if (targetDm && c.projectName === targetDm.agentId && name !== targetId) {
              c.projectName = name;
            }
            if (parentId) {
              c.parentProjectId = parentId;
              c.parentProjectName = parentName;
            }
          });
        }

        for (const fwd of items) {
          /*
           * [v1.0.23.1] ★ 转发 ≠ 前端本地插一条消息。转发 = 用户发了一条消息
           *   （配言即 content），走 sendMessage 的正常路径（socket 出站 → 后端 → Agent）。
           *   LLM 模板由**后端构造**；前端只发结构化 forwarded 载荷：
           *   content = 配言（comment）、originalText = 完整原文、sourceProjectName = 来源群名。
           *   气泡显示配言（displayText）+ 引用窗（forwarded）；回声按 cmid 命中，气泡不被顶替。
           */
          get().sendMessage(comment || '', targetId, undefined, {
            displayText: comment || '',
            forwarded: {
              sourceName: fwd.sourceName,
              ...(fwd.sourceProjectName ? { sourceProjectName: fwd.sourceProjectName } : {}),
              originalText: forwardOriginal(fwd),
              comment: comment || '',
              markdown: fwd.markdown,
              ...(fwd.sourceRef ? { sourceRef: fwd.sourceRef } : {}),
            },
            ...(fwd.files && fwd.files.length ? { files: fwd.files } : {}),
          });
        }
      }
    },
  })),
);
