/**
 * ContextMenu.tsx — [v0.40.0] 浮层一族：右键菜单 / Toast / 遮罩弹窗 / 转发选择 / 标签编辑
 *
 * 视觉与行为**逐条照抄** reference/Knowe-UI_v1.3.reference.html（README §3.5/§4.7 铁律）：
 *   · 菜单 DOM（component-tree §I·ContextMenu）：.menu > (.mi > .mic + .mtx + [.mchev|.mk]) | .msep
 *   · 位置自适应：place()（2257 行）——右/下越界就翻回来，pad 8px
 *   · 关闭动画：.menu.closing 120ms 后移除（closeMenu，2284 行）
 *   · 子菜单：hover 200ms 后从 .mi 右侧飞出（r.right-4, r.top-4），离开 250ms 后若不在
 *     子菜单上则收回（buildMenuItems，2294–2325 行）
 *   · 点击项：先闪一下 accent-tint，80ms 后关菜单再执行（2317 行）
 *   · 全局关闭：点空白 mousedown / 滚动(捕获) / Escape（3457–3461、3445 行；
 *     Escape 优先级 scrim > menu > 多选，照 3447–3451 的顺序）
 *   · Toast：普通提示 2400ms 后加 .out、320ms 后移除；删除工作态长留至 Promise 落定
 *   · 遮罩弹窗：.scrim(.center) > .modal；点遮罩空白 mousedown 关闭；.out 200ms（2416–2423 行）
 *   · confirmModal / 转发弹窗（forwardPicker，2584–2611 行）：标题「转发到」+ 搜索 +
 *     .pick-list 多选 + 取消/发送
 *
 * 层容器样式抄自 reference 的 #menuLayer / #toastLayer（743 / 805 行），
 * 以 .menu-layer / .ctx-toast-layer 类追加进 knowe-components.css（不与既有 ToastHost 抢 id）。
 *
 * 数据边界：本文件只 import store（转发弹窗要读会话列表）与共享原子件，遵守
 * 「components 只碰 store/selectors」的既有铁律。
 */

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import { createPortal } from 'react-dom';
import { create } from 'zustand';
import { useKnoweStore } from '../store/store';
import {
  PLATFORM_PROJECT_ID, ZINNIA_AVATAR, getZinniaDisplayName, faceFor, isCoordinator,
} from '../store/avatar';
import type { Conv, Member, ForwardItem } from '../store/state';
import { dmGroupOf, dmSessionId, isAgentDm, isPrivateChat } from '../store/chat';
import { useRecordsStore } from '../store/records';
import { Avatar, AvatarGrid, type GridMember } from './Avatar';
import { IconCheck, IconChevR, IconAlert, IconSpark, IconAt } from './icons';
import { Markdown } from './markdown';

// ── [v0.44.8] convMenu 权威参考中的五枚线性图标（路径逐条照抄 v1.3） ──
const IconPin: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 17v5" /><path d="M9 10.8V4h6v6.8l2 2.2H7z" />
  </svg>
);
const IconBellOff: React.FC<{ small?: boolean }> = ({ small }) => (
  <svg width={small ? 13 : 16} height={small ? 13 : 16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M8.7 3.5A6 6 0 0 1 18 8.5c0 3 .7 4.9 1.5 6M17.5 17.5H5s2-1.5 2-9M10.3 21a2 2 0 0 0 3.4 0" />
    <path d="M3 3l18 18" />
  </svg>
);
const IconFold: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="4" width="18" height="4" rx="1.5" /><path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8" />
  </svg>
);
const IconEdit: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 20h9" /><path d="M16.5 3.5a2 2 0 0 1 3 3L7 19l-4 1 1-4z" />
  </svg>
);
const IconBook: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H12v15H5.5A1.5 1.5 0 0 1 4 17.5z" />
    <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H12v15h6.5a1.5 1.5 0 0 0 1.5-1.5z" />
  </svg>
);
// ── [v0.44.9] agentMenu 权威参考中的三枚线性图标（路径逐条照抄 v1.3 ICON 表） ──
const IconAgentMessage: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);
const IconAgentProfile: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M13 5l7 7-7 7" /><path d="M20 12H5a1 1 0 0 0-1 1v4" />
  </svg>
);
const IconAgentReport: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
    <path d="M14 3v4h4M8.5 13h7M8.5 16.5h5" />
  </svg>
);
const IconTrash: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 15H6L5 6" />
    <path d="M10 11v6M14 11v6" />
  </svg>
);
/**
 * `.menu > .mi`、menuIn/menuOut、fold-entry 等均逐条取自 Knowe-UI v1.3；
 * 后半段只补这次 DOM 委托桥需要的宿主/静默/折叠规则。
 */
const CONVERSATION_MENU_CSS = `
.menu-layer{position:fixed;inset:0;z-index:200;pointer-events:none}
.menu{
  position:absolute;pointer-events:auto;
  min-width:212px;max-width:280px;
  background:var(--surface);border-radius:var(--r-md);
  box-shadow:var(--shadow-3);padding:var(--space-1);
  transform-origin:top left;
  animation:menuIn var(--dur-base) var(--ease-out) both;
}
@keyframes menuIn{from{opacity:0;transform:scale(0.98) translateY(-2px)}to{opacity:1;transform:scale(1) translateY(0)}}
.menu.closing{animation:menuOut var(--dur-micro) var(--ease-out) both}
@keyframes menuOut{to{opacity:0;transform:scale(0.99)}}
.mi{
  display:flex;align-items:center;gap:var(--space-3);
  height:34px;padding:0 var(--space-3);border-radius:var(--r-sm);
  font-size:14px;line-height:20px;color:var(--ink);cursor:pointer;
  transition:background var(--dur-micro) var(--ease-out),color var(--dur-micro) var(--ease-out);
  position:relative;
}
.mi .mic{width:16px;height:16px;color:var(--ink-2);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.mi .mtx{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mi .mk{font-size:11px;font-weight:500;color:var(--ink-3);flex-shrink:0;font-variant-numeric:tabular-nums}
.mi .mchev{color:var(--ink-3);flex-shrink:0}
.mi .mtick{color:var(--accent);flex-shrink:0;opacity:0;transition:opacity var(--dur-micro)}
.mi.on .mtick{opacity:1}
.mi:hover,.mi.hl{background:var(--accent-tint)}
.mi:hover .mic,.mi.hl .mic{color:var(--accent)}
.mi.danger{color:var(--danger)}.mi.danger .mic{color:var(--danger)}
.mi.danger:hover,.mi.danger.hl{background:var(--danger-bg)}
.mi.danger:hover .mic{color:var(--danger)}
.msep{height:1px;background:var(--hairline);opacity:0.7;margin:var(--space-1) var(--space-2)}

.citem.pinned{background:var(--surface-2)}
.citem.pinned:hover{background:var(--surface-sunken)}
.cname .bell{color:var(--ink-3);flex-shrink:0;display:flex}
.cname .pin-ic{margin-left:auto;color:var(--ink-3);flex-shrink:0;display:flex}
.fold-entry{
  display:flex;align-items:center;gap:var(--space-2);
  padding:10px var(--space-4);margin:var(--space-1) var(--space-1) 0;
  border-radius:var(--r-md);cursor:pointer;color:var(--ink-2);
  transition:background var(--dur-micro);
}
.fold-entry:hover{background:var(--surface-2)}
.fold-entry .chev{transition:transform var(--dur-base) var(--ease-out)}
.fold-entry.open .chev{transform:rotate(90deg)}
.fold-entry .lbl{font-size:13px;flex:1}
.fold-entry .n{font-size:11px;color:var(--ink-3)}
.fold-body{overflow:hidden;max-height:0;transition:max-height var(--dur-soft) var(--ease-out)}
.fold-body .citem .cname{color:var(--ink-2)}

.knowe-conv-order-host{display:flex!important;flex-direction:column!important}
.knowe-conv-order-host>.grp-label{order:2000}
.knowe-conv-fold-news{font-size:11px;color:var(--danger);white-space:nowrap}
.knowe-conv-menu-icon{pointer-events:none}
.citem[data-knowe-menu-hidden="true"]{
  display:none!important;
}
.citem.knowe-conv-muted .unread-dot,
.citem.knowe-conv-muted .unread-pill,
.citem.knowe-conv-muted .at-badge,
.citem.knowe-conv-muted .await,
.citem.knowe-conv-muted .mute-dot,
.citem.knowe-conv-muted .status-dot,
.citem.knowe-conv-muted .agent-status,
.citem.knowe-conv-muted .cstatus,
.citem.knowe-conv-muted [data-agent-status],
.citem.knowe-conv-muted [data-unread],
.citem.knowe-conv-muted [class*="unread"],
.citem.knowe-conv-folded .unread-dot,
.citem.knowe-conv-folded .unread-pill,
.citem.knowe-conv-folded .at-badge,
.citem.knowe-conv-folded .await,
.citem.knowe-conv-folded .mute-dot,
.citem.knowe-conv-folded .status-dot,
.citem.knowe-conv-folded .agent-status,
.citem.knowe-conv-folded .cstatus,
.citem.knowe-conv-folded [data-agent-status],
.citem.knowe-conv-folded [data-unread],
.citem.knowe-conv-folded [class*="unread"]{display:none!important}
.knowe-conv-fold-body{height:0;min-height:0;margin:0;padding:0;border:0;pointer-events:none}

/* 删除确认补充说明 / 删除等待态：样式逐条取自 reference 的 .role-note / .spinner。 */
.modal .role-note{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-3);margin-bottom:var(--space-5)}
.modal .role-note svg{color:var(--accent);flex-shrink:0}
.ctx-toast-layer .spinner{width:14px;height:14px;border:2px solid var(--hairline);border-top-color:var(--ink-3);border-radius:50%;animation:spin 0.8s linear infinite;display:inline-block;vertical-align:-2px}
@keyframes spin{to{transform:rotate(360deg)}}
`;

// ═══════════════════════════════════════════════════════════════
// 菜单数据模型（与 reference 的 items 数组一一对应）
// ═══════════════════════════════════════════════════════════════

export interface MenuAction {
  icon?: React.ReactNode;
  label: string;
  danger?: boolean;
  /** 右侧快捷键小字（如 ⌘C）——只展示，不真的绑按键（照抄 reference）。 */
  key?: string;
  /** 有 sub → 右侧箭头 + hover 飞出子菜单。 */
  sub?: MenuEntry[];
  onClick?: () => void;
}
export type MenuEntry = MenuAction | '---';

type ToastKind = 'ok' | 'info' | 'warn';

interface FloatToast {
  id: number;
  text: string;
  kind: ToastKind;
  out: boolean;
  busy: boolean;
}

interface FloatState {
  menu: { items: MenuEntry[]; x: number; y: number; nonce: number } | null;
  menuClosing: boolean;
  modal: React.ReactNode | null;
  modalCenter: boolean;
  modalClosing: boolean;
  toasts: FloatToast[];
}

const useFloatStore = create<FloatState>(() => ({
  menu: null,
  menuClosing: false,
  modal: null,
  modalCenter: true,
  modalClosing: false,
  toasts: [],
}));

let _nonce = 0;
let _toastId = 0;
let _menuCloseTimer: number | undefined;

// ── 菜单 ──

export function openMenu(items: MenuEntry[], x: number, y: number): void {
  window.clearTimeout(_menuCloseTimer);
  useFloatStore.setState({ menu: { items, x, y, nonce: ++_nonce }, menuClosing: false });
}

export function closeMenu(): void {
  const { menu, menuClosing } = useFloatStore.getState();
  if (!menu || menuClosing) return;
  // 照抄 reference：加 .closing，120ms 后移除
  useFloatStore.setState({ menuClosing: true });
  _menuCloseTimer = window.setTimeout(() => {
    useFloatStore.setState({ menu: null, menuClosing: false });
  }, 120);
}

// ── Toast ──

//: [v0.44.1 Bug4] 同时最多显示 5 条操作 toast；第 6 条出现即挤掉最老的。
const MAX_CTX_TOASTS = 5;
const TOAST_STAY_MS = 2400;
const TOAST_OUT_MS = 320;
const MODAL_OUT_MS = 200;

//: [v0.44.1 Bug4] toast 层从右下角移到**屏幕中央偏下**（内联样式盖掉 CSS 的右下定位）。
//: column 自底向上叠，条数增减时自然保持居中偏下。
const CTX_TOAST_LAYER_STYLE: React.CSSProperties = {
  position: 'fixed',
  left: '50%',
  right: 'auto',
  bottom: '24vh',
  transform: 'translateX(-50%)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 10,
  zIndex: 9000,
  pointerEvents: 'none',
  maxWidth: '92vw',
};

function appendToast(text: string, kind: ToastKind, busy: boolean): number {
  const id = ++_toastId;
  useFloatStore.setState((s) => {
    const toasts = [...s.toasts, { id, text, kind, out: false, busy }];
    // 长留工作态不能被普通 Toast 挤掉；超限时只淘汰最早的非工作态。
    while (toasts.length > MAX_CTX_TOASTS) {
      const removable = toasts.findIndex((item) => !item.busy);
      if (removable < 0) break;
      toasts.splice(removable, 1);
    }
    return { toasts };
  });
  return id;
}

function scheduleToastOut(id: number, delay = TOAST_STAY_MS): void {
  window.setTimeout(() => {
    useFloatStore.setState((s) => ({
      toasts: s.toasts.map((t) => (t.id === id ? { ...t, out: true } : t)),
    }));
    window.setTimeout(() => {
      useFloatStore.setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, TOAST_OUT_MS);
  }, delay);
}

export function toast(text: string, kind: ToastKind = 'ok'): void {
  scheduleToastOut(appendToast(text, kind, false));
}

/** 长留的工作中提示；只由完成/失败结果主动收束，不设自动消失计时器。 */
function workingToast(text: string): number {
  return appendToast(text, 'info', true);
}

/** 把同一条长留提示自然替换为结果，再沿用 reference 的 2400ms + 320ms 退出节奏。 */
function settleWorkingToast(id: number, text: string, kind: ToastKind): void {
  let found = false;
  useFloatStore.setState((s) => ({
    toasts: s.toasts.map((t) => {
      if (t.id !== id) return t;
      found = true;
      return { ...t, text, kind, busy: false, out: false };
    }),
  }));
  if (!found) {
    toast(text, kind);
    return;
  }
  scheduleToastOut(id);
}

// ── 遮罩弹窗 ──

function openModal(node: React.ReactNode, center = true): void {
  useFloatStore.setState({ modal: node, modalCenter: center, modalClosing: false });
}

export function closeModal(): void {
  const { modal, modalClosing } = useFloatStore.getState();
  if (!modal || modalClosing) return;
  useFloatStore.setState({ modalClosing: true });
  window.setTimeout(() => {
    useFloatStore.setState({ modal: null, modalClosing: false });
  }, MODAL_OUT_MS);
}

export interface ConfirmOpts {
  title: string;
  body: string;
  note?: string;
  okLabel?: string;
  danger?: boolean;
  onOk?: () => void;
}

/** 确认弹窗（照抄 reference confirmModal：取消/确认，danger 时确认钮红底）。 */
export function confirmModal(opts: ConfirmOpts): void {
  openModal(<ConfirmModalBody {...opts} />, true);
}

const ConfirmModalBody: React.FC<ConfirmOpts> = ({ title, body, note, okLabel, danger, onOk }) => {
  const { t } = useTranslation();
  return (
    <div className="modal">
      <div className="modal-title">{title}</div>
      <div className="modal-body" style={note ? { marginBottom: 'var(--space-3)' } : undefined}>
        {body}
      </div>
      {note ? (
        <div className="role-note">
          <IconSpark />
          <span>{note}</span>
        </div>
      ) : null}
      <div className="modal-acts">
        <button
          className="btn btn-ghost"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={closeModal}
        >
          {t('chat.stream.03')}
        </button>
        <button
          className="btn btn-primary"
          style={danger
            ? { flex: 'none', padding: '0 20px', background: 'var(--danger)' }
            : { flex: 'none', padding: '0 20px' }}
          onClick={() => { closeModal(); onOk?.(); }}
        >
          {okLabel || t('common.20')}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// [v0.44.8] 群聊列表 convMenu + renameConv
// ═══════════════════════════════════════════════════════════════

/** 复用聊天头部既有「在聊天中查找」路径；自定义事件给完整工程的抽屉控制器一个直达入口。 */
function openConversationRecords(projectId: string): void {
  const st = useKnoweStore.getState();
  st.setView('chats');
  st.switchProject(projectId);
  window.setTimeout(() => {
    window.dispatchEvent(new CustomEvent('knowe:open-conversation-records', {
      detail: { projectId, tab: i18n.t('common.15') },
    }));
    const button = document.querySelector<HTMLElement>(
      `.chat-card [aria-label="${i18n.t('chat.stream.04')}"],`
      + `.chat-card [data-tip="${i18n.t('chat.stream.05')}"],`
      + `.chat-card button[aria-label*="${i18n.t('chat.stream.05')}"]`,
    );
    button?.click();
  }, 320);
}

export function openConversationMenu(projectId: string, x: number, y: number): void {
  const st = useKnoweStore.getState();
  if (!projectId || projectId === PLATFORM_PROJECT_ID || !st.projectOrder.includes(projectId)) return;
  const pinned = Object.prototype.hasOwnProperty.call(st.pinnedProjects, projectId);
  const muted = !!st.mutedProjects[projectId];
  const folded = !!st.foldedProjects[projectId];

  // 顺序和 reference convMenu 完全一致；本次明确删除 win / tag / trash 三项。
  openMenu([
    {
      icon: <IconPin />,
      label: pinned ? i18n.t('context.menu.14') : i18n.t('context.menu.07'),
      onClick: () => {
        void useKnoweStore.getState().setProjectPinned(projectId, !pinned).then((ok) => {
          if (ok) toast(!pinned ? i18n.t('context.menu.21') : i18n.t('context.menu.17'));
          else toast(i18n.t('store.20'), 'warn');
        });
      },
    },
    {
      icon: <IconBellOff />,
      label: muted ? i18n.t('context.menu.13') : i18n.t('context.menu.04'),
      onClick: () => {
        void useKnoweStore.getState().setProjectMuted(projectId, !muted).then((ok) => {
          if (ok) toast(!muted ? i18n.t('context.menu.18') : i18n.t('context.menu.16'));
          else toast(i18n.t('store.04'), 'warn');
        });
      },
    },
    {
      icon: <IconFold />,
      label: folded ? i18n.t('context.menu.38') : i18n.t('context.menu.27'),
      onClick: () => {
        void useKnoweStore.getState().setProjectFolded(projectId, !folded).then((ok) => {
          if (ok) toast(!folded ? i18n.t('context.menu.19') : i18n.t('context.menu.20'));
          else toast(i18n.t('store.08'), 'warn');
        });
      },
    },
    '---',
    {
      icon: <IconBook />,
      label: i18n.t('context.menu.24'),
      onClick: () => openConversationRecords(projectId),
    },
    {
      icon: <IconEdit />,
      label: i18n.t('common.22'),
      onClick: () => openRenameConversation(projectId),
    },
  ], x, y);
}

// ── [v0.44.9] Agent 头像右键菜单 ──────────────────────────────────────────────

/** 私聊窗口里仍以父项目作为 Agent 菜单的项目上下文，避免生成 dm:dm:… 嵌套会话。 */
function agentProjectContext(projectId: string): string {
  return isAgentDm(projectId) ? (dmGroupOf(projectId) || '') : projectId;
}

/**
 * [v1.0.24.1 修复] 查看资料 → 联系人资料页。
 * 旧实现是 DOM 桥接（按排序复刻找 .grp-row/.navrow 再模拟点击）——依赖 DOM 结构与
 * ContactsView 内部排序完全一致，任何失配（排序差异/折叠态/成员 id 不匹配）都静默失败，
 * 停在 ContactsView 初始选中（知知）→ 表现为「右键任意 agent 都跳到知知资料」。
 * 改为走官方通道：dispatch window 事件 → App.navigateFromSearch({kind:'contact'})
 * → searchFocus → ContactsView 直接 setSelected（含展开分组/下钻），与全局搜索跳转同一路径。
 */
function selectAgentProfile(projectId: string, agentId: string, zinnia: boolean): void {
  useKnoweStore.getState().setView('contacts');
  window.dispatchEvent(new CustomEvent('knowe:focus-contact', {
    detail: zinnia
      ? { projectId: null, agentId: null }   // 知知：ContactsView 回初始知知
      : { projectId, agentId },
  }));
}

function openAgentRecords(projectId: string, agentId: string, zinnia: boolean): void {
  const st = useKnoweStore.getState();
  const records = useRecordsStore.getState();
  records.setCategory('all');
  records.setSearchQuery('');
  records.setSelectedDate(null);
  if (zinnia) st.switchProject(PLATFORM_PROJECT_ID);
  else st.enterDm(projectId, agentId);
  records.openDrawer();
  st.setView('chats');
}

interface DeleteFeedbackOpts {
  pending: string;
  success: string;
  failure: string;
  action: () => Promise<boolean>;
  onDeleted?: () => void;
}

/**
 * 确认窗按 reference 用 200ms 淡出；淡出完成后才挂长留 Toast 并发起删除，
 * 避免两个浮层视觉冲突。Promise 落定后原位替换成成功/失败提示。
 */
function runDeleteWithFeedback(opts: DeleteFeedbackOpts): void {
  window.setTimeout(() => {
    const toastId = workingToast(opts.pending);
    void Promise.resolve().then(opts.action).then(
      (ok) => {
        settleWorkingToast(toastId, ok ? opts.success : opts.failure, ok ? 'ok' : 'warn');
        if (ok) {
          try { opts.onDeleted?.(); } catch { /* 视图收敛失败不应反转已完成的删除结果 */ }
        }
      },
      () => settleWorkingToast(toastId, opts.failure, 'warn'),
    );
  }, MODAL_OUT_MS);
}

// ── [v0.45] 联系人视图专用菜单 ───────────────────────────────────────────────
// 这两条入口只由 ContactsView 调用；聊天列表继续使用 openConversationMenu，
// 头像/气泡继续使用 openAgentMenu，三套菜单互不串改。

export function openContactGroupMenu(
  projectId: string, x: number, y: number, onDeleted?: () => void,
): void {
  const st = useKnoweStore.getState();
  const conv = st.convs[projectId];
  if (!projectId || projectId === PLATFORM_PROJECT_ID || isPrivateChat(projectId) || !conv) return;

  openMenu([
    {
      icon: <IconBook />,
      label: i18n.t('context.menu.29'),
      onClick: () => openConversationRecords(projectId),
    },
    '---',
    {
      icon: <IconTrash />,
      label: i18n.t('context.menu.02'),
      danger: true,
      onClick: () => confirmModal({
        title: i18n.t('context.menu.23'),
        body: i18n.t('context.menu.36'),
        note: i18n.t('context.menu.45'),
        okLabel: i18n.t('context.menu.37'),
        danger: true,
        onOk: () => runDeleteWithFeedback({
          pending: i18n.t('context.menu.32'),
          success: i18n.t('context.menu.41'),
          failure: i18n.t('context.menu.39'),
          action: () => useKnoweStore.getState().deleteProjectPermanently(projectId),
          onDeleted,
        }),
      }),
    },
  ], x, y);
}

export function openContactAgentMenu(
  projectId: string, agentId: string, x: number, y: number, onDeleted?: () => void,
): void {
  const st = useKnoweStore.getState();
  const conv = st.convs[projectId];
  const member = conv?.members?.find((candidate) => (
    candidate.id === agentId && candidate.status !== 'removed'
  ));
  if (!projectId || projectId === PLATFORM_PROJECT_ID || !agentId || !conv || !member) return;
  if (agentId.toLowerCase() === 'zinnia') return;

  openMenu([
    {
      icon: <IconAgentMessage />,
      label: i18n.t('context.menu.05'),
      onClick: () => {
        const current = useKnoweStore.getState();
        current.enterDm(projectId, agentId);
        useRecordsStore.getState().closeDrawer();
        current.setView('chats');
      },
    },
    {
      icon: <IconBook />,
      label: i18n.t('context.menu.29'),
      onClick: () => openAgentRecords(projectId, agentId, false),
    },
    '---',
    {
      icon: <IconTrash />,
      label: i18n.t('context.menu.02'),
      danger: true,
      onClick: () => confirmModal({
        title: i18n.t('context.menu.22'),
        body: i18n.t('context.menu.35'),
        note: i18n.t('context.menu.44'),
        okLabel: i18n.t('context.menu.37'),
        danger: true,
        onOk: () => runDeleteWithFeedback({
          pending: i18n.t('context.menu.31'),
          success: i18n.t('context.menu.09'),
          failure: i18n.t('context.menu.08'),
          action: () => useKnoweStore.getState().deleteAgentPermanently(projectId, agentId),
          onDeleted,
        }),
      }),
    },
  ], x, y);
}

/** 知知只显示资料/记录；项目经理与 Worker 显示完整四项。 */
export function openAgentMenu(projectId: string, agentId: string, x: number, y: number): void {
  if (!agentId) return;
  const zinnia = agentId.toLowerCase() === 'zinnia' || projectId === PLATFORM_PROJECT_ID;
  const contextId = zinnia ? PLATFORM_PROJECT_ID : agentProjectContext(projectId);
  if (!zinnia && !contextId) return;

  const st = useKnoweStore.getState();
  const agentMember = zinnia ? null : st.convs[contextId]?.members?.find((m) => m.id === agentId) || null;
  const agentName = zinnia
    ? getZinniaDisplayName()
    : memberNameLabel(agentId, agentMember?.display.name || agentId);
  const common: MenuEntry[] = [
    {
      icon: <IconAgentProfile />,
      label: i18n.t('context.menu.30'),
      onClick: () => selectAgentProfile(contextId, agentId, zinnia),
    },
    {
      icon: <IconBook />,
      label: i18n.t('context.menu.29'),
      onClick: () => openAgentRecords(contextId, agentId, zinnia),
    },
  ];

  if (zinnia) {
    openMenu(common, x, y);
    return;
  }

  openMenu([
    {
      icon: <IconAgentMessage />,
      label: `${i18n.t('context.menu.05')} ${agentName}`,
      onClick: () => {
        const current = useKnoweStore.getState();
        current.enterDm(contextId, agentId);
        useRecordsStore.getState().closeDrawer();
        current.setView('chats');
      },
    },
    // [v1.0.23.9] 快捷 @ 该 agent：发 CustomEvent，Composer 监听后把
    // @备注名 插进输入框光标处（避免每次都去 mention-picker 里翻）。
    {
      icon: <IconAt />,
      label: `@ ${agentName}`,
      onClick: () => {
        window.dispatchEvent(new CustomEvent('knowe:insert-mention', {
          detail: { agentId },
        }));
      },
    },
    ...common,
    '---',
    {
      icon: <IconAgentReport />,
      label: i18n.t('context.menu.42'),
      onClick: () => {
        const current = useKnoweStore.getState();
        const records = useRecordsStore.getState();
        if (current.activeProjectId !== contextId) current.switchProject(contextId);
        records.setCategory('handoff');
        records.setSearchQuery('');
        records.setSelectedDate(null);
        records.openDrawer();
        current.setView('chats');
      },
    },
  ], x, y);
}

export function openRenameConversation(projectId: string): void {
  const conv = useKnoweStore.getState().convs[projectId];
  if (!conv || projectId === PLATFORM_PROJECT_ID) return;
  openModal(
    <RenameConversationModalBody
      projectId={projectId}
      initialName={conv.projectName || projectId}
    />,
    true,
  );
}

const RenameConversationModalBody: React.FC<{
  projectId: string;
  initialName: string;
}> = ({ projectId, initialName }) => {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initialName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, []);

  const commit = async (): Promise<void> => {
    const name = value.trim();
    if (!name) { setError(t('context.menu.40')); return; }
    if (busy) return;
    setBusy(true);
    setError('');
    const ok = await useKnoweStore.getState().renameProject(projectId, name);
    setBusy(false);
    if (!ok) {
      setError(t('context.menu.46'));
      inputRef.current?.focus();
      return;
    }
    closeModal();
    toast(t('common.toastRenamed'));
  };

  return (
    <div className="modal">
      <div className="modal-title">{t('common.22')}</div>
      <input
        ref={inputRef}
        className="modal-input"
        value={value}
        maxLength={80}
        disabled={busy}
        onChange={(event) => { setValue(event.target.value); if (error) setError(''); }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
            event.preventDefault();
            void commit();
          }
        }}
      />
      {error ? <div className="modal-body" style={{ color: 'var(--danger)', paddingTop: 8 }}>{error}</div> : null}
      <div className="modal-acts">
        <button
          className="btn btn-ghost"
          style={{ flex: 'none', padding: '0 20px' }}
          disabled={busy}
          onClick={closeModal}
        >
          {t('chat.stream.03')}
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 'none', padding: '0 20px' }}
          disabled={busy || !value.trim()}
          onClick={() => { void commit(); }}
        >
          {busy ? t('context.menu.12') : t('context.menu.11')}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// 转发弹窗（README §3.3；DOM/交互照抄 reference forwardPicker 2584 行）
// ═══════════════════════════════════════════════════════════════

/**
 * 打开转发弹窗（README §2/§9）。items = 要转发的内容（单条 / 多选 / 收藏卡都归一到 ForwardItem[]）。
 * 目标列表 = 知知（唯一、置顶、不重复）+ projectOrder 里的群；群可展开选其中的项目经理/Worker。
 * 点发送 → store.forwardMessages 把内容投进每个目标会话（带原格式）。
 */
export function openForwardPicker(items: ForwardItem[]): void {
  openModal(<ForwardModalBody items={items} />, true);
}

/** 群头像 = 聊天列表同一套宫格（v0.39.1 #3 的既定原则），等比缩进 32px 的 pick-row 头像位。 */
function gridMembersOf(conv: Conv): GridMember[] {
  const live = (conv.members || []).filter((m: Member) => m.status !== 'removed');
  const sorted = [...live].sort(
    (a, b) => Number(isCoordinator(b.id)) - Number(isCoordinator(a.id)),
  );
  return sorted.map((m) => ({
    id: m.id,
    glyph: m.display.glyph,
    pal: m.display.pal,
    avatarUrl: m.display.avatarUrl ?? faceFor(m.id, conv.projectId, conv.projectName).avatarUrl,
  }));
}

/** 群内成员（项目经理在前），转发弹窗下钻用。 */
function liveMembersSorted(conv: Conv): Member[] {
  const live = (conv.members || []).filter((m) => m.status !== 'removed');
  return [...live].sort((a, b) => Number(isCoordinator(b.id)) - Number(isCoordinator(a.id)));
}

/** [v1.0.23.2] 成员显示名：总管走 faceFor（「{项目名} · 总管」中文），其余用花名册名。 */
function memberDisplayName(m: Member, projectId: string, projectName?: string): string {
  if (isCoordinator(m.id)) {
    return faceFor(m.id, projectId, projectName).name ?? m.display.name;
  }
  return memberNameLabel(m.id, m.display.name || faceFor(m.id, projectId, projectName).name || m.id);
}

const ForwardModalBody: React.FC<{ items: ForwardItem[] }> = ({ items }) => {
  const { t } = useTranslation();
  const convs = useKnoweStore((s) => s.convs);
  const projectOrder = useKnoweStore((s) => s.projectOrder);
  const [q, setQ] = useState('');
  const [chosen, setChosen] = useState<Record<string, true>>({});
  const [expanded, setExpanded] = useState<Record<string, true>>({});
  // [v1.0.23.1] 附言 + @ 选择器（PRD R1/R2）
  const [comment, setComment] = useState('');
  const [atOpen, setAtOpen] = useState(false);
  const [atQuery, setAtQuery] = useState('');
  // [v1.0.23.1] 附言框高度：null=默认（min-height 60px），拖拽后固定 px
  const [commentH, setCommentH] = useState<number | null>(null);
  // [v1.0.23.2] 下边界拖拽（替代原生右下角 resize 手柄——右下角是取消/发送按钮，易误触）
  const resizeDragRef = useRef<{ startY: number; startH: number } | null>(null);
  const startCommentResize = (e: React.MouseEvent<HTMLDivElement>): void => {
    e.preventDefault();
    const input = atWrapRef.current?.querySelector('textarea');
    if (!input) return;
    const startH = input.offsetHeight;
    resizeDragRef.current = { startY: e.clientY, startH };
    const onMove = (ev: MouseEvent): void => {
      const d = resizeDragRef.current;
      if (!d) return;
      const h = Math.min(180, Math.max(60, d.startH + (ev.clientY - d.startY)));
      setCommentH(h);
    };
    const onUp = (): void => {
      resizeDragRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };
  // [v1.0.23.1] 点击浮层/按钮之外的任意处 → 关闭 @ 成员浮层（不强制必须选人）
  const atWrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!atOpen) return;
    const onDocMouseDown = (e: MouseEvent): void => {
      if (atWrapRef.current && !atWrapRef.current.contains(e.target as Node)) {
        setAtOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [atOpen]);

  const kw = q.trim();
  const toggle = (id: string): void => setChosen((prev) => {
    const next = { ...prev };
    if (next[id]) delete next[id]; else next[id] = true;
    return next;
  });
  const toggleExpand = (pid: string): void => setExpanded((prev) => {
    const next = { ...prev };
    if (next[pid]) delete next[pid]; else next[pid] = true;
    return next;
  });

  // 群列表（有成员的项目）；知知单列，不进这里、也不从 projectOrder 里重复取（#2c）。
  const groups = projectOrder
    .filter((pid) => pid !== PLATFORM_PROJECT_ID)
    .map((pid) => convs[pid])
    .filter((c): c is Conv => !!c && (c.members?.some((m) => m.status !== 'removed') ?? false));

  const zin = convs[PLATFORM_PROJECT_ID];
  const zinName = zin?.projectName || t('common.19');
  const zinShown = !kw || zinName.includes(kw);

  // 搜索命中群名 → 整群显示；命中某成员名 → 该群显示并自动展开、只列命中成员（#2b 搜索 Agent）。
  const groupRows = groups
    .map((conv) => {
      const nameHit = !kw || (conv.projectName || conv.projectId).includes(kw);
      const members = liveMembersSorted(conv);
      const hitMembers = members.filter((m) => !kw || m.display.name.includes(kw));
      const show = nameHit || hitMembers.length > 0;
      const showMembers = kw
        ? (nameHit ? members : hitMembers)     // 群名命中 → 展开全部；否则只列命中成员
        : members;
      const autoOpen = !!kw && !nameHit && hitMembers.length > 0;
      return { conv, show, members: showMembers, autoOpen };
    })
    .filter((g) => g.show);

  // [v1.0.23.1] @ 选择器：归属已勾选的群聊目标（最后一个勾选的群）；未选群时不可用。
  const atGroups = groupRows.filter((g) => chosen[g.conv.projectId]);
  const atTargetGroup = atGroups[atGroups.length - 1];
  const atMembers = atTargetGroup
    ? liveMembersSorted(atTargetGroup.conv).filter((m) => !atQuery || m.display.name.includes(atQuery))
    : [];

  // 输入 @（或点击 @ 按钮）→ 打开成员浮层；当前词不再是 @ 开头 → 关闭。
  const handleCommentChange = (e: React.ChangeEvent<HTMLTextAreaElement>): void => {
    const v = e.target.value;
    setComment(v);
    const lastWord = (v.split(/[\s\n]/).pop() ?? '').trim();
    if (lastWord.startsWith('@')) {
      setAtQuery(lastWord.slice(1));
      setAtOpen(true);
    } else {
      setAtOpen(false);
    }
  };

  // 点选成员 → 末尾有 @查询词则替换、无则直接追加「@名字 」，关闭浮层。
  const pickAt = (m: Member): void => {
    setComment((prev) => {
      const lastWord = (prev.split(/[\s\n]/).pop() ?? '').trim();
      return lastWord.startsWith('@')
        ? prev.replace(/@[^\s\n]*$/, `@${m.display.name} `)
        : `${prev}@${m.display.name} `;
    });
    setAtOpen(false);
  };

  const n = Object.keys(chosen).length;

  const send = (): void => {
    closeModal();
    if (!n) return;
    useKnoweStore.getState().forwardMessages(Object.keys(chosen), items, comment.trim());
    toast(t('context.menu.forwardedTo', { n }));
  };

  return (
    <div className="modal" style={{ width: 'min(480px,92vw)' }}>
      <div className="modal-title">{t('context.menu.43')}</div>
      <input
        className="modal-input"
        placeholder={t('context.menu.26')}
        value={q}
        autoFocus
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="pick-list">
        {/* 知知：唯一一条，永远置顶，不重复（#2c） */}
        {zinShown && (
          <div
            className={'pick-row' + (chosen[PLATFORM_PROJECT_ID] ? ' on' : '')}
            onClick={() => toggle(PLATFORM_PROJECT_ID)}
          >
            <Avatar glyph={zinName.charAt(0) || '知'} pal="av-a" size={32} src={ZINNIA_AVATAR} />
            <span className="pick-nm">{zinName}</span>
            <span className="pick-check"><IconCheck /></span>
          </div>
        )}

        {/* 群聊：整行可勾（转发给整群）；左侧箭头就地展开选成员（转发给某成员的私聊） */}
        {groupRows.map(({ conv, members, autoOpen }) => {
          const pid = conv.projectId;
          const gname = conv.projectName || pid;
          const open = !!expanded[pid] || autoOpen;
          return (
            <div key={pid}>
              <div className={'pick-row' + (chosen[pid] ? ' on' : '')}>
                <span
                  className="fwd-drill"
                  role="button"
                  tabIndex={0}
                  aria-label={open ? t('contacts.view.16') : t('contacts.view.10')}
                  onClick={(e) => { e.stopPropagation(); toggleExpand(pid); }}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); toggleExpand(pid); } }}
                  style={{ transform: open ? 'rotate(90deg)' : 'none' }}
                >
                  <IconChevR />
                </span>
                <span
                  style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flex: 1, minWidth: 0, cursor: 'pointer' }}
                  onClick={() => toggle(pid)}
                >
                  <span
                    style={{ width: 32, height: 32, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                  >
                    <span style={{ transform: `scale(${32 / 44})` }}>
                      <AvatarGrid members={gridMembersOf(conv)} title={gname} />
                    </span>
                  </span>
                  <span className="pick-nm">{gname}</span>
                  <span className="pick-check"><IconCheck /></span>
                </span>
              </div>
              {open && (
                <>
                {/* [v1.0.23.2] 成员行小字提醒：勾选 Agent = 发私聊，不是发群聊 */}
                <div className="fwd-dm-hint">勾选 Agent 后，消息将发送至私聊窗口，而非群聊</div>
                {members.map((m) => {
                const dmId = dmSessionId(pid, m.id);
                return (
                  <div
                    key={dmId}
                    className={'pick-row fwd-member' + (chosen[dmId] ? ' on' : '')}
                    onClick={() => toggle(dmId)}
                  >
                    <Avatar
                      glyph={m.display.glyph}
                      pal={m.display.pal}
                      size={28}
                      src={m.display.avatarUrl ?? faceFor(m.id, pid, conv.projectName).avatarUrl}
                    />
                    <span className="pick-nm">
                      {memberDisplayName(m, pid, conv.projectName)}
                      {roleLabel(m.display.role) ? <span className="fwd-role"> · {roleLabel(m.display.role)}</span> : null}
                    </span>
                    <span className="pick-check"><IconCheck /></span>
                  </div>
                );
                })}
                </>
              )}
            </div>
          );
        })}

        {kw && !zinShown && groupRows.length === 0 && (
          <div style={{ padding: '12px 8px', fontSize: 13, color: 'var(--ink-3)' }}>{t('context.menu.33')}</div>
        )}
        </div>
        {/* [v1.0.23.1] 附言输入框 + @ 群成员选择（PRD R1/R2） */}
        <div className="fwd-comment" ref={atWrapRef}>
          <textarea
            className="fwd-comment-input"
            placeholder="添加附言…（可选）"
            value={comment}
            rows={2}
            style={commentH != null ? { height: commentH } : undefined}
            onChange={handleCommentChange}
          />
          <button
            type="button"
            className={'fwd-at-btn' + (atOpen ? ' on' : '')}
            title={atTargetGroup ? `@ ${atTargetGroup.conv.projectName || '群'} 的成员` : '先勾选群聊目标'}
            disabled={!atTargetGroup}
            onClick={() => { setAtQuery(''); setAtOpen((v) => !v); }}
          >
            @
          </button>
          {atOpen && (
            <div className="fwd-at-pop">
              <div className="fwd-at-pop-h">
                {atTargetGroup
                  ? `@ ${atTargetGroup.conv.projectName || '群'} 的成员`
                  : '先勾选群聊目标'}
              </div>
              {atMembers.length === 0 && (
                <div style={{ padding: '8px', fontSize: 12, color: 'var(--ink-3)' }}>没有匹配的成员</div>
              )}
              {atMembers.map((m) => {
                const g = atTargetGroup;
                if (!g) return null;
                return (
                  <div
                    key={m.id}
                    className="fwd-at-row"
                    role="button"
                    tabIndex={0}
                    onClick={() => pickAt(m)}
                    onKeyDown={(e) => { if (e.key === 'Enter') pickAt(m); }}
                  >
                    <Avatar
                      glyph={m.display.glyph}
                      pal={m.display.pal}
                      size={28}
                      src={m.display.avatarUrl ?? faceFor(m.id, g.conv.projectId, g.conv.projectName).avatarUrl}
                    />
                    <span className="pick-nm">{memberDisplayName(m, g.conv.projectId, g.conv.projectName)}</span>
                    {m.display.role ? <span className="fwd-role"> · {roleLabel(m.display.role)}</span> : null}
                  </div>
                );
              })}
            </div>
          )}
          {/* [v1.0.23.2] 下边界拖拽条：整条可拖，替代原生右下角手柄（避免误触取消/发送） */}
          <div
            className="fwd-resize-h"
            title="拖动调整附言框高度"
            onMouseDown={startCommentResize}
          />
        </div>
        <div className="modal-acts">
        <button
          className="btn btn-ghost"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={closeModal}
        >
          {t('chat.stream.03')}
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={send}
        >
          {t('composer.04')}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// 标签编辑弹窗（README §4.4-3：可输入标签的小弹窗，确认后更新卡片标签）
// ═══════════════════════════════════════════════════════════════

export function openTagEditor(current: string[], onOk: (tags: string[]) => void): void {
  openModal(<TagModalBody current={current} onOk={onOk} />, true);
}

const TagModalBody: React.FC<{ current: string[]; onOk: (tags: string[]) => void }> = ({
  current, onOk,
}) => {
  const { t } = useTranslation();
  const [val, setVal] = useState(current.join(' '));
  const commit = (): void => {
    // 空格/逗号分隔；统一成 #xx 形态（与 reference 侧栏 '#设计' 的写法一致）。
    const tags = val
      .split(/[\s,，]+/)
      .map((t) => t.trim())
      .filter(Boolean)
      .map((t) => (t.startsWith('#') ? t : `#${t}`));
    closeModal();
    onOk(Array.from(new Set(tags)));
  };
  return (
    <div className="modal" style={{ width: 'min(420px,92vw)' }}>
      <div className="modal-title">{t('context.menu.06')}</div>
      <input
        className="modal-input"
        placeholder={t('context.menu.34')}
        value={val}
        autoFocus
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') commit(); }}
      />
      <div className="modal-acts">
        <button
          className="btn btn-ghost"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={closeModal}
        >
          {t('chat.stream.03')}
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={commit}
        >
          {t('common.20')}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// 通用输入弹窗（[v0.40.1 #5] 收藏卡「重命名」用；DOM 沿用 .modal + .modal-input）
// ═══════════════════════════════════════════════════════════════

export interface InputModalOpts {
  title: string;
  initial?: string;
  placeholder?: string;
  okLabel?: string;
  onOk: (value: string) => void;
}

export function openInputModal(opts: InputModalOpts): void {
  openModal(<InputModalBody {...opts} />, true);
}

const InputModalBody: React.FC<InputModalOpts> = ({ title, initial, placeholder, okLabel, onOk }) => {
  const { t } = useTranslation();
  const [val, setVal] = useState(initial ?? '');
  const commit = (): void => {
    const v = val.trim();
    closeModal();
    if (v) onOk(v);
  };
  return (
    <div className="modal" style={{ width: 'min(420px,92vw)' }}>
      <div className="modal-title">{title}</div>
      <input
        className="modal-input"
        placeholder={placeholder}
        value={val}
        autoFocus
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') commit(); }}
      />
      <div className="modal-acts">
        <button
          className="btn btn-ghost"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={closeModal}
        >
          {t('chat.stream.03')}
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={commit}
        >
          {okLabel || t('common.20')}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// [v0.40.2 #3] 新建笔记弹窗（收藏页「＋ 新建笔记」）
//   动画/骨架与转发弹窗一致（openModal → .scrim.center > .modal：雾面遮罩 + 居中白色圆角卡片）。
//   横向输入区域：左边自由输入、右边即时 markdown 预览（格式反馈）；取消 / 确认。
// ═══════════════════════════════════════════════════════════════

export function openNoteComposer(onOk: (text: string) => void): void {
  openModal(<NoteComposerModalBody onOk={onOk} />, true);
}

const NoteComposerModalBody: React.FC<{ onOk: (text: string) => void }> = ({ onOk }) => {
  const { t } = useTranslation();
  const [val, setVal] = useState('');
  const commit = (): void => {
    const v = val.trim();
    closeModal();
    if (v) onOk(v);
  };
  // 两栏共用的盒子度量（编辑区 / 预览区各占一半，窄屏自动换行堆叠）。
  const paneStyle: React.CSSProperties = {
    flex: '1 1 260px', minWidth: 0, boxSizing: 'border-box',
    minHeight: 200, maxHeight: 340, borderRadius: 10, padding: '10px 12px',
    fontSize: 14, lineHeight: 1.6, color: 'var(--ink)',
  };
  return (
    <div className="modal" style={{ width: 'min(680px,94vw)' }}>
      <div className="modal-title">{t('context.menu.28')}</div>
      <div style={{ fontSize: 12, color: 'var(--ink-3)', margin: '2px 0 10px' }}>
        {t('context.menu.markdownHint')}
      </div>
      {/* 横向输入区域：左边写，右边即时看到 markdown 渲染出来的基本格式反馈。 */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <textarea
          className="note-editor"
          placeholder={t('context.menu.15')}
          value={val}
          autoFocus
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') commit(); }}
          style={{
            ...paneStyle, resize: 'vertical', fontFamily: 'inherit',
            background: 'var(--surface-2)', border: '1px solid var(--hairline)', outline: 'none',
          }}
        />
        <div
          className="fav-md"
          style={{
            ...paneStyle, overflowY: 'auto',
            background: 'transparent', border: '1px dashed var(--hairline)',
          }}
          aria-hidden="true"
        >
          {val.trim()
            ? <Markdown text={val} />
            : <span style={{ color: 'var(--ink-3)' }}>{t('context.menu.47')}</span>}
        </div>
      </div>
      <div className="modal-acts">
        <button
          className="btn btn-ghost"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={closeModal}
        >
          {t('chat.stream.03')}
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 'none', padding: '0 20px' }}
          onClick={commit}
        >
          {t('common.20')}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// 摆放：place() —— 逐字复刻 reference 2257 行的翻转规则
// ═══════════════════════════════════════════════════════════════

function place(node: HTMLElement, x: number, y: number, pad = 8): void {
  const w = node.offsetWidth;
  const h = node.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (x + w + pad > vw) x = Math.max(pad, x - w);
  if (y + h + pad > vh) y = Math.max(pad, vh - h - pad);
  node.style.left = `${Math.max(pad, x)}px`;
  node.style.top = `${Math.max(pad, y)}px`;
}

// ═══════════════════════════════════════════════════════════════
// 菜单面板（含子菜单飞出）
// ═══════════════════════════════════════════════════════════════

const MenuPanel: React.FC<{
  items: MenuEntry[];
  x: number;
  y: number;
  closing?: boolean;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}> = ({ items, x, y, closing, onMouseEnter, onMouseLeave }) => {
  const ref = useRef<HTMLDivElement>(null);
  // 先按原始坐标渲染，量完尺寸再翻转（reference 是 append 后 place，同一件事）。
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) place(el, x, y);
  }, [x, y, items]);

  return (
    <div
      ref={ref}
      className={'menu' + (closing ? ' closing' : '')}
      style={{ left: x, top: y }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {items.map((it, i) => (it === '---'
        ? <div className="msep" key={`sep${i}`} />
        : <MenuItemRow key={`${it.label}${i}`} item={it} />))}
    </div>
  );
};

const MenuItemRow: React.FC<{ item: MenuAction }> = ({ item }) => {
  const miRef = useRef<HTMLDivElement>(null);
  const [sub, setSub] = useState<{ x: number; y: number } | null>(null);
  const [flash, setFlash] = useState(false);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
  }, []);

  if (item.sub) {
    /*
     * [v0.40.2 修复#2] ★ 子菜单要**真的飞出来**，而不是消失在别处。
     *
     *   真根因是**定位坐标系**：`.menu` 自己是 `position:absolute`，而子菜单也是
     *   `position:absolute` 的 `.menu`。旧写法把子菜单当作父菜单 DOM 的**子节点**渲染，
     *   于是它的 left/top（来自 getBoundingClientRect 的**视口坐标**）被解释成「相对父菜单
     *   左上角」——等于把偏移量叠了两遍，子菜单被推到父菜单外老远、多半飞出屏幕，看起来就是
     *   「没有二级菜单」。
     *
     *   reference 的做法是 `menuLayer().appendChild(subEl)`——把子菜单挂到**菜单层**
     *   （fixed inset:0，本身就是视口坐标系）里、和主菜单**平级**，视口坐标就对上了。
     *   这里用 createPortal 把子菜单送进 `.menu-layer`（找不到再退回 body），一模一样。
     *
     *   收放沿用「父项/子菜单任一被 hover 就取消关闭，两边都离开 250ms 后才收」，
     *   鼠标从父项挪到子菜单的那点空隙里不会被收走。右箭头 mchev 照 reference 一直渲染。
     */
    const openSub = (): void => {
      const r = miRef.current?.getBoundingClientRect();
      if (r) setSub({ x: r.right - 4, y: r.top - 4 });
    };
    const cancelClose = (): void => window.clearTimeout(closeTimer.current);
    const scheduleClose = (): void => {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = window.setTimeout(() => setSub(null), 250);
    };
    return (
      <>
        <div
          ref={miRef}
          className={'mi' + (item.danger ? ' danger' : '')}
          onMouseEnter={() => {
            cancelClose();
            openTimer.current = window.setTimeout(openSub, 200);
          }}
          onMouseLeave={() => {
            window.clearTimeout(openTimer.current);
            scheduleClose();
          }}
        >
          <span className="mic">{item.icon}</span>
          <span className="mtx">{item.label}</span>
          <span className="mchev"><IconChevR /></span>
        </div>
        {sub && createPortal(
          <MenuPanel
            items={item.sub}
            x={sub.x}
            y={sub.y}
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          />,
          document.querySelector('.menu-layer') ?? document.body,
        )}
      </>
    );
  }

  return (
    <div
      className={'mi' + (item.danger ? ' danger' : '')}
      style={flash ? { background: 'var(--accent-tint)' } : undefined}
      onClick={() => {
        // 照抄 reference：先闪 accent-tint，80ms 后关菜单再执行。
        setFlash(true);
        window.setTimeout(() => { closeMenu(); item.onClick?.(); }, 80);
      }}
    >
      <span className="mic">{item.icon}</span>
      <span className="mtx">{item.label}</span>
      {item.key ? <span className="mk">{item.key}</span> : null}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// 宿主：菜单层 + 遮罩层 + Toast 层（App 挂一次）
// ═══════════════════════════════════════════════════════════════

const KIND_ICON: Record<ToastKind, React.ReactNode> = {
  ok: <IconCheck />,
  warn: <IconAlert />,
  info: <IconSpark />,
};

// ── [v0.44.8] 缺失 ConvList 源文件时的无侵入桥：只借既有 .citem[data-conv] DOM ──

const NATIVE_CONVERSATION_LIST_SELECTOR = '[data-knowe-native-conversation-list="true"]';
const PIN_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"></path><path d="M9 10.8V4h6v6.8l2 2.2H7z"></path></svg>';
const BELL_OFF_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8.7 3.5A6 6 0 0 1 18 8.5c0 3 .7 4.9 1.5 6M17.5 17.5H5s2-1.5 2-9M10.3 21a2 2 0 0 0 3.4 0"></path><path d="M3 3l18 18"></path></svg>';
const CHEV_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>';

interface ConversationDom {
  host: HTMLElement;
  cards: Map<string, HTMLElement>;
}

/** 当前包已有原生 ConvList 集成时，撤掉旧 DOM 桥可能留下的纯布局副作用。 */
function clearLegacyConversationBridge(root: HTMLElement): void {
  const hosts = [
    root,
    ...Array.from(root.querySelectorAll<HTMLElement>('.knowe-conv-order-host')),
  ];
  for (const host of hosts) {
    host.classList.remove('knowe-conv-order-host');
    for (const child of Array.from(host.children)) {
      if (!(child instanceof HTMLElement)) continue;
      if (child.matches('.citem[data-conv],.grp-label')) child.style.removeProperty('order');
      if (child.classList.contains('knowe-pinned-fold-entry')
          || child.classList.contains('knowe-folded-fold-entry')
          || child.classList.contains('knowe-conv-fold-body')) child.remove();
    }
  }
  for (const icon of Array.from(root.querySelectorAll<HTMLElement>('[data-knowe-conv-icon]'))) {
    icon.remove();
  }
}

function findConversationDom(): ConversationDom | null {
  // ConvList.tsx 已原生渲染置顶/折叠/菜单时，绝不能再让兼容桥猜宿主。
  // DM 模式只有一张群卡，旧算法会误把 .dm-panel 当 flex 排序宿主并颠倒子元素。
  if (document.querySelector(NATIVE_CONVERSATION_LIST_SELECTOR)) return null;
  const state = useKnoweStore.getState();
  const known = new Set(state.projectOrder);
  const candidates = Array.from(document.querySelectorAll<HTMLElement>('.citem[data-conv]'))
    .filter((card) => known.has(card.dataset.conv || ''));
  if (!candidates.length) return null;

  // 虚拟列表/完整列表实现都可能多包一层；以「承载已知项目卡最多」的直接父元素为宿主。
  const counts = new Map<HTMLElement, number>();
  for (const card of candidates) {
    const parent = card.parentElement;
    if (parent) counts.set(parent, (counts.get(parent) || 0) + 1);
  }
  let host: HTMLElement | null = null;
  let best = 0;
  for (const [parent, count] of counts) {
    if (count > best) { host = parent; best = count; }
  }
  if (!host) return null;

  const cards = new Map<string, HTMLElement>();
  for (const card of candidates) {
    if (card.parentElement !== host) continue;
    const pid = card.dataset.conv;
    if (pid) cards.set(pid, card);
  }
  return cards.size ? { host, cards } : null;
}

function setCardIcon(
  card: HTMLElement, kind: 'muted' | 'pinned', enabled: boolean,
): void {
  const own = card.querySelector<HTMLElement>(`[data-knowe-conv-icon="${kind}"]`);
  if (!enabled) { own?.remove(); return; }
  const name = card.querySelector<HTMLElement>('.cname')
    || card.querySelector<HTMLElement>('.cbody');
  if (!name) return;
  const nativeClass = kind === 'muted' ? '.bell' : '.pin-ic';
  if (own || name.querySelector(nativeClass)) return;
  const icon = document.createElement('span');
  icon.className = `${kind === 'muted' ? 'bell' : 'pin-ic'} knowe-conv-menu-icon`;
  icon.dataset.knoweConvIcon = kind;
  icon.setAttribute('aria-label', kind === 'muted' ? i18n.t('context.menu.04') : i18n.t('context.menu.07'));
  icon.innerHTML = kind === 'muted' ? BELL_OFF_SVG : PIN_SVG;
  name.appendChild(icon);
}

function ensureFoldEntry(host: HTMLElement, kind: 'pinned' | 'folded'): HTMLElement {
  let entry = Array.from(host.children).find(
    (node): node is HTMLElement => node instanceof HTMLElement
      && node.dataset.knoweConvEntry === kind,
  );
  if (entry) return entry;
  entry = document.createElement('div');
  entry.className = `fold-entry knowe-${kind}-fold-entry`;
  entry.dataset.knoweConvEntry = kind;
  entry.setAttribute('role', 'button');
  entry.tabIndex = 0;
  entry.innerHTML = `<span class="chev">${CHEV_SVG}</span><span class="lbl"></span><span class="n"></span><span class="knowe-conv-fold-news"></span>`;
  const toggle = (event: Event): void => {
    event.preventDefault();
    event.stopPropagation();
    const latest = useKnoweStore.getState();
    if (kind === 'pinned') latest.togglePinnedCollapsed();
    else latest.toggleFoldedOpen();
  };
  entry.addEventListener('click', toggle);
  entry.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') toggle(event);
  });
  host.appendChild(entry);
  return entry;
}

function ensureFoldBody(host: HTMLElement): HTMLElement {
  let body = Array.from(host.children).find(
    (node): node is HTMLElement => node instanceof HTMLElement
      && node.dataset.knoweConvEntry === 'fold-body',
  );
  if (body) return body;
  body = document.createElement('div');
  body.className = 'fold-body knowe-conv-fold-body';
  body.dataset.knoweConvEntry = 'fold-body';
  body.setAttribute('aria-hidden', 'true');
  host.appendChild(body);
  return body;
}

function removeFoldEntry(host: HTMLElement, kind: string): void {
  for (const node of Array.from(host.children)) {
    if (node instanceof HTMLElement && node.dataset.knoweConvEntry === kind) node.remove();
  }
}

function setNodeText(node: HTMLElement | null, text: string): void {
  // MutationObserver 监听 childList；即便值相同，反复写 textContent 也可能
  // 触发“观察器 → 重排 → 观察器”的空转循环，所以只在内容真正变化时写入。
  if (node && node.textContent !== text) node.textContent = text;
}

function decorateConversationList(): void {
  const nativeHost = document.querySelector<HTMLElement>(NATIVE_CONVERSATION_LIST_SELECTOR);
  if (nativeHost) {
    clearLegacyConversationBridge(nativeHost);
    return;
  }
  const found = findConversationDom();
  if (!found) return;
  const { host, cards } = found;
  const state = useKnoweStore.getState();
  host.classList.add('knowe-conv-order-host');

  // 删除旧宿主上的桥接节点，避免 ConvList 切视图/重建后留下幽灵入口。
  for (const node of Array.from(document.querySelectorAll<HTMLElement>('[data-knowe-conv-entry]'))) {
    if (node.parentElement !== host) node.remove();
  }

  const pinned = state.projectOrder
    .filter((pid) => Object.prototype.hasOwnProperty.call(state.pinnedProjects, pid));
  const folded = state.projectOrder.filter((pid) => !!state.foldedProjects[pid]);
  // 普通区沿用 ConvList 自己的动态顺序（例如正在工作的项目临时上浮）；CSS 只把
  // 置顶区和折叠区钉在两端，保证现有“新状态上浮”不会被菜单桥抹掉。
  const normal = Array.from(cards.keys()).filter(
    (pid) => !state.pinnedProjects[pid] && !state.foldedProjects[pid],
  );
  const pinnedCollapsed = pinned.length >= 3 && state.pinnedCollapsed;

  pinned.forEach((pid, index) => {
    const card = cards.get(pid);
    if (card) card.style.order = String(1000 + index);
  });
  normal.forEach((pid, index) => {
    const card = cards.get(pid);
    if (card) card.style.order = String(2100 + index);
  });
  folded.forEach((pid, index) => {
    const card = cards.get(pid);
    if (card) card.style.order = String(3010 + index);
  });

  for (const [pid, card] of cards) {
    const isPinned = Object.prototype.hasOwnProperty.call(state.pinnedProjects, pid);
    const isMuted = !!state.mutedProjects[pid];
    const isFolded = !!state.foldedProjects[pid];
    const hidden = (isPinned && pinnedCollapsed) || (isFolded && !state.foldedOpen);
    card.dataset.knoweMenuProject = pid;
    card.classList.toggle('pinned', isPinned);
    card.classList.toggle('knowe-conv-muted', isMuted);
    card.classList.toggle('knowe-conv-folded', isFolded);
    card.dataset.knoweMenuHidden = hidden ? 'true' : 'false';
    if (hidden) card.setAttribute('aria-hidden', 'true');
    else card.removeAttribute('aria-hidden');
    setCardIcon(card, 'muted', isMuted);
    setCardIcon(card, 'pinned', isPinned);
  }

  // 「项目」标签必须永远位于置顶区和普通区之间。
  for (const label of Array.from(host.children)) {
    if (label instanceof HTMLElement && label.classList.contains('grp-label')) {
      label.style.order = '2000';
    }
  }

  if (pinned.length >= 3) {
    const entry = ensureFoldEntry(host, 'pinned');
    entry.style.order = '1900';
    entry.classList.toggle('open', !pinnedCollapsed);
    entry.setAttribute('aria-expanded', String(!pinnedCollapsed));
    const label = entry.querySelector<HTMLElement>('.lbl');
    const count = entry.querySelector<HTMLElement>('.n');
    const news = entry.querySelector<HTMLElement>('.knowe-conv-fold-news');
    setNodeText(label, pinnedCollapsed ? i18n.t('conv.list.pinnedCount', { n: pinned.length }) : i18n.t('context.menu.25'));
    setNodeText(count, pinnedCollapsed ? '' : String(pinned.length));
    const hasUnread = pinnedCollapsed && pinned.some((pid) => (state.convs[pid]?.unread || 0) > 0);
    setNodeText(news, hasUnread ? i18n.t('context.menu.01') : '');
  } else {
    removeFoldEntry(host, 'pinned');
  }

  if (folded.length) {
    const entry = ensureFoldEntry(host, 'folded');
    entry.style.order = '3000';
    entry.classList.toggle('open', state.foldedOpen);
    entry.setAttribute('aria-expanded', String(state.foldedOpen));
    const label = entry.querySelector<HTMLElement>('.lbl');
    const count = entry.querySelector<HTMLElement>('.n');
    const news = entry.querySelector<HTMLElement>('.knowe-conv-fold-news');
    setNodeText(label, i18n.t('context.menu.03'));
    setNodeText(count, String(folded.length));
    setNodeText(news, '');
    const body = ensureFoldBody(host);
    body.style.order = '3001';
    body.classList.toggle('open', state.foldedOpen);
  } else {
    removeFoldEntry(host, 'folded');
    removeFoldEntry(host, 'fold-body');
  }
}

function mutationTouchesConversationList(records: MutationRecord[]): boolean {
  for (const record of records) {
    const target = record.target instanceof Element ? record.target : null;
    if (target?.closest('.knowe-conv-order-host')) return true;
    for (const node of Array.from(record.addedNodes)) {
      if (!(node instanceof Element)) continue;
      if (node.matches('.citem[data-conv],.grp-label')
          || node.querySelector('.citem[data-conv],.grp-label')) return true;
    }
  }
  return false;
}

export const FloatingLayers: React.FC = () => {
  const menu = useFloatStore((s) => s.menu);
  const menuClosing = useFloatStore((s) => s.menuClosing);
  const modal = useFloatStore((s) => s.modal);
  const modalCenter = useFloatStore((s) => s.modalCenter);
  const modalClosing = useFloatStore((s) => s.modalClosing);
  const toasts = useFloatStore((s) => s.toasts);
  const syncConversationStates = useKnoweStore((s) => s.syncConversationStates);
  const conversationBridgeSignature = useKnoweStore((s) => [
    s.pinnedCollapsed ? 'pc1' : 'pc0',
    s.foldedOpen ? 'fo1' : 'fo0',
    ...s.projectOrder.map((pid) => [
      pid,
      Object.prototype.hasOwnProperty.call(s.pinnedProjects, pid) ? s.pinnedProjects[pid] : '-',
      s.mutedProjects[pid] ? 'm1' : 'm0',
      s.foldedProjects[pid] ? 'f1' : 'f0',
      s.convs[pid]?.unread || 0,
    ].join(':')),
  ].join('|'));

  // 后端是 pin/mute/fold 的唯一真源；浮层宿主随 App 常驻，开机只需在这里对账一次。
  useEffect(() => { void syncConversationStates(); }, [syncConversationStates]);

  // 仅给“没有原生 ConvList 集成”的旧壳保留 DOM 桥；当前包会由 data 标记直接短路。
  useEffect(() => {
    const nativeHost = document.querySelector<HTMLElement>(NATIVE_CONVERSATION_LIST_SELECTOR);
    if (nativeHost) {
      clearLegacyConversationBridge(nativeHost);
      return undefined;
    }
    let raf = 0;
    const schedule = (): void => {
      if (raf) return;
      raf = window.requestAnimationFrame(() => {
        raf = 0;
        decorateConversationList();
      });
    };
    const observer = new MutationObserver((records) => {
      if (mutationTouchesConversationList(records)) schedule();
    });
    observer.observe(document.body, { childList: true, subtree: true });

    const onConversationContextMenu = (event: MouseEvent): void => {
      const target = event.target instanceof Element ? event.target : null;
      const card = target?.closest<HTMLElement>('.citem[data-conv]');
      if (card?.closest(NATIVE_CONVERSATION_LIST_SELECTOR)) return;
      const projectId = card?.dataset.conv || '';
      const state = useKnoweStore.getState();
      if (!card || !projectId || projectId === PLATFORM_PROJECT_ID
          || !state.projectOrder.includes(projectId)) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      openConversationMenu(projectId, event.clientX, event.clientY);
    };
    document.addEventListener('contextmenu', onConversationContextMenu, true);
    schedule();
    return () => {
      observer.disconnect();
      document.removeEventListener('contextmenu', onConversationContextMenu, true);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, []);

  useEffect(() => {
    const raf = window.requestAnimationFrame(decorateConversationList);
    return () => window.cancelAnimationFrame(raf);
  }, [conversationBridgeSignature]);

  // 全局关闭：Escape（scrim > 菜单 > 多选，照 reference 3447–3451 的优先级）/
  //           点空白 mousedown / 滚动(捕获)。
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return;
      const st = useFloatStore.getState();
      if (st.modal) { closeModal(); return; }
      if (st.menu) { closeMenu(); return; }
      if (useKnoweStore.getState().selecting) useKnoweStore.getState().exitSelect();
    };
    const onDown = (e: MouseEvent): void => {
      const st = useFloatStore.getState();
      if (st.menu && !(e.target as HTMLElement | null)?.closest?.('.menu')) closeMenu();
    };
    const onScroll = (): void => { if (useFloatStore.getState().menu) closeMenu(); };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onDown);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, []);

  return createPortal(
    <>
      <style data-knowe-conv-menu-style>{CONVERSATION_MENU_CSS}</style>
      {/* 菜单层：抄 #menuLayer（fixed inset 0，pointer-events:none；.menu 自身恢复交互） */}
      <div className="menu-layer">
        {menu && (
          <MenuPanel
            key={menu.nonce}
            items={menu.items}
            x={menu.x}
            y={menu.y}
            closing={menuClosing}
          />
        )}
      </div>

      {/* 遮罩 + 弹窗（转发 / 确认 / 标签）。点遮罩空白关闭（mousedown，照抄 reference）。 */}
      {modal && (
        <div
          className={'scrim' + (modalCenter ? ' center' : '') + (modalClosing ? ' out' : '')}
          onMouseDown={(e) => { if (e.target === e.currentTarget) closeModal(); }}
        >
          {modal}
        </div>
      )}

      {/* Toast 层：[v0.44.1 Bug4] 从右下角改为屏幕中央偏下（内联样式接管定位）。 */}
      <div className="ctx-toast-layer" style={CTX_TOAST_LAYER_STYLE}>
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}` + (t.out ? ' out' : '')}>
            {t.busy ? <span className="spinner" aria-hidden="true" /> : KIND_ICON[t.kind]}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </>,
    document.body,
  );
};

export default FloatingLayers;
