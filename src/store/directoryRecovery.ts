/**
 * directoryRecovery.ts — 目录恢复的独立状态容器（[v0.13 卡片]）
 *
 * 为什么单独一个 store，而不是并进主 store（../store/store）：
 *   目录失效是**服务器级控制流**，不进聊天时间线、不占 seq 水位——它跟消息/审批不是一类东西。
 *   把它单独关在这里，主 store 一个字段都不用动；App / ConvList / 卡片三处直接订阅这里即可。
 *
 * 刻意不依赖 zustand、也不依赖 React 18 的 useSyncExternalStore：
 *   用最朴素的「模块级 state + 订阅集 + useState/useEffect 订阅」实现，React 16.8+ 通用，
 *   不给宿主工程引入任何版本耦合。
 *
 * 一条项目的目录待处理态由两样东西描述：
 *   · request  —— 卡片要用的最小信息（有它 = 侧边栏亮红字「未处理事项」）
 *   · openUntil —— 卡片「展开」到什么时间点（毫秒时间戳）。
 *       now < openUntil  → 展开的完整卡片（5 分钟倒计时）
 *       now >= openUntil → 收起成顶部一条红色「未处理事项」细条（点它可重开）
 *
 * 状态机（对应需求 1/2/3/6）：
 *   project_directory_required 事件           → openRequest：设 request + openUntil = now+5min（弹卡）
 *   倒计时归零 / 用户点「拒绝」                → cancel：回传 cancel_project_directory + 收起（红字留着）
 *   点顶部红条 / 侧边栏项（可选）              → reopen：openUntil = now+5min（重新弹卡）
 *   project_directory_restored 事件           → resolve：整条清除（红字消失）
 *   握手/冷启动 project_created.directory_required → syncFromProjectCreated：重建 request（默认收起）
 */

import { useEffect, useState } from 'react';

/** 5 分钟倒计时（需求 2）。展开卡片与重开都用它。 */
export const DIRECTORY_CARD_DURATION_MS = 5 * 60 * 1000;

/** 卡片要用的最小信息（顶层 project_id/project_name 归一后一起塞进来）。 */
export interface DirectoryRequest {
  projectId: string;
  projectName: string;
  previousDir: string;
  reason: string;
  requestId: string;
}

export interface DirectoryEntry {
  request: DirectoryRequest;
  /** 卡片展开截止时间戳（ms）。<= now 视为已收起（只剩红字）。 */
  openUntil: number;
}

/** App 建好 socket 后注入这两个方法（镜像主 store 的 setSocket）。 */
export interface DirectorySocket {
  setProjectDirectory: (projectId: string, directory: string, requestId: string, projectName?: string) => void;
  cancelProjectDirectory: (projectId: string, requestId?: string) => void;
}

interface State {
  /** projectId → 待处理条目。不在表里 = 该项目目录正常。 */
  entries: Record<string, DirectoryEntry>;
}

// ── 模块级单例状态 + 订阅 ──
let state: State = { entries: {} };
const listeners = new Set<() => void>();
let socket: DirectorySocket | null = null;

function emit(): void {
  for (const l of listeners) l();
}
function setEntries(entries: Record<string, DirectoryEntry>): void {
  state = { entries };
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function getState(): State {
  return state;
}

export function getEntry(projectId: string | null | undefined): DirectoryEntry | undefined {
  return projectId ? state.entries[projectId] : undefined;
}

// ═══════════════════════════════════════════════════════════════
// actions
// ═══════════════════════════════════════════════════════════════

/** App 注入 socket 的目录命令。 */
export function bindSocket(api: DirectorySocket | null): void {
  socket = api;
}

/** 归一：把 project_directory_required 事件收进来并**弹开**卡片。 */
export function openRequest(ev: {
  project_id: string; project_name: string;
  previous_dir: string; reason: string; request_id: string;
}): void {
  const req: DirectoryRequest = {
    projectId: ev.project_id,
    projectName: ev.project_name,
    previousDir: ev.previous_dir,
    reason: ev.reason,
    requestId: ev.request_id,
  };
  setEntries({
    ...state.entries,
    [ev.project_id]: { request: req, openUntil: Date.now() + DIRECTORY_CARD_DURATION_MS },
  });
}

/**
 * 握手/冷启动：project_created 若带 directory_required，就（重）建这条待处理态。
 *   · 带 → 确保 request 存在；**保留**已有的 openUntil（正在展开的卡不因一条 project_created 被收起），
 *     本来没有就默认收起（openUntil=0）——重连后先亮红字，卡片交给用户点开。
 *   · 不带 → 该项目目录此刻有效，清掉任何残留（这也是「恢复」在重连路径上的信号）。
 */
export function syncFromProjectCreated(ev: {
  project_id: string;
  project_name?: string;
  directory_required?: { previous_dir: string; reason: string; request_id: string };
}): void {
  const pid = ev.project_id;
  const info = ev.directory_required;
  if (info) {
    const prev = state.entries[pid];
    const req: DirectoryRequest = {
      projectId: pid,
      projectName: ev.project_name ?? prev?.request.projectName ?? pid,
      previousDir: info.previous_dir,
      reason: info.reason,
      requestId: info.request_id,
    };
    setEntries({
      ...state.entries,
      [pid]: { request: req, openUntil: prev?.openUntil ?? 0 },
    });
  } else if (state.entries[pid]) {
    const next = { ...state.entries };
    delete next[pid];
    setEntries(next);
  }
}

/** 收起卡片（保留 request → 红字还在）。 */
export function collapse(projectId: string): void {
  const e = state.entries[projectId];
  if (!e || e.openUntil === 0) return;
  setEntries({ ...state.entries, [projectId]: { ...e, openUntil: 0 } });
}

/** 重新展开卡片（需求 6：点卡片入口重开）。倒计时重新计。 */
export function reopen(projectId: string): void {
  const e = state.entries[projectId];
  if (!e) return;
  setEntries({
    ...state.entries,
    [projectId]: { ...e, openUntil: Date.now() + DIRECTORY_CARD_DURATION_MS },
  });
}

/** 目录已恢复：整条清除。 */
export function resolve(projectId: string): void {
  if (!state.entries[projectId]) return;
  const next = { ...state.entries };
  delete next[projectId];
  setEntries(next);
}

/**
 * 确认：把新目录（可选新名字）回传后端。
 *   不在这里改本地态——成功会等 project_directory_restored → resolve；
 *   目录非法后端会重发 project_directory_required → openRequest（卡片自动再弹）。
 *   卡片组件自己在提交期间置「提交中」禁用按钮防抖。
 */
export function confirm(projectId: string, directory: string, projectName?: string): void {
  const e = state.entries[projectId];
  if (!e || !socket) return;
  socket.setProjectDirectory(projectId, directory, e.request.requestId, projectName);
}

/**
 * 取消 / 超时：回传 cancel_project_directory，团队保持暂缓；本地收起（红字留着，不循环弹）。
 * 需求 6 的核心：取消 = 暂缓，但留一个随时可点开的入口。
 */
export function cancel(projectId: string): void {
  const e = state.entries[projectId];
  if (!e) return;
  socket?.cancelProjectDirectory(projectId, e.request.requestId);
  collapse(projectId);
}

// ═══════════════════════════════════════════════════════════════
// hooks（订阅式；projectId 变化会重新选择并重订阅）
// ═══════════════════════════════════════════════════════════════

/** 取某项目的待处理条目（含 openUntil）。无 = undefined。卡片组件用。 */
export function useDirectoryEntry(projectId: string | null | undefined): DirectoryEntry | undefined {
  const [entry, setEntry] = useState<DirectoryEntry | undefined>(() => getEntry(projectId));
  useEffect(() => {
    const update = (): void => setEntry(getEntry(projectId));
    update(); // 订阅建立前的窗口里状态可能已变，先对齐一次
    return subscribe(update);
  }, [projectId]);
  return entry;
}

/** 某项目是否处于目录待处理态（有 request 即真）。侧边栏红字用——只关心真假，不关心展开与否。 */
export function useDirectoryPending(projectId: string | null | undefined): boolean {
  const [pending, setPending] = useState<boolean>(() => !!getEntry(projectId));
  useEffect(() => {
    const update = (): void => setPending(!!getEntry(projectId));
    update();
    return subscribe(update);
  }, [projectId]);
  return pending;
}
