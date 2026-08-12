/**
 * ChatStream.tsx — 聊天主区（component-tree §C · ChatCard 头部 + MessageArea）
 *
 * DOM：header.chat-head(.scrolled) > (.ch-info > .ch-title + .ch-status)
 *                                   + (.ch-actions > [.stack] + button.icon-btn ×2)
 *      .msgs-wrap > [.banner] + .msgs + button.fab-bottom(.show)
 *
 * 数据：makeSelectItems / makeSelectMembers / makeSelectConvName / makeSelectBanner
 *      （[v1.0.23.5] per-session 订阅工厂；组件不碰 transport，也不碰 contract）
 *
 * 分发规则（Item 联合类型 → 组件）：
 *   system                     → SystemLine
 *   approval                   → ApprovalCard
 *   agent + streaming          → StreamBubble（阶段 + 三点输入反馈）
 *   agent + 空 text + 不流     → 不渲染（§2.1#3 空气泡守卫，双保险）
 *   user / agent               → MessageBubble
 */

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState, useDeferredValue } from 'react';
import { useCachedT } from '../i18n';
import { useKnoweStore } from '../store/store';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import {
  makeSelectItems, makeSelectMembers,
  makeSelectConvName, makeSelectBanner,
} from '../store/selectors';
import type { Item, Member, ForwardItem } from '../store/state';
import { Avatar } from './Avatar';
import { IconSearchSm, IconDown, IconRecover, IconX, IconClip, IconChevR } from './icons';
import AddAgentsPopover from './AddAgentsPopover';
import MessageBubble, { type AgentFace } from './MessageBubble';
import { faceFor, isCoordinator } from '../store/avatar';
import { SessionActiveContext } from './sessionActiveContext';

/** [v1.0.24.4-r14] 慢→快→慢 曲线（cubic-bezier(0.77,0,0.175,1) 采样）——派卡接力动画用。 */
function easeCubic(t: number): number {
  const x1 = 0.77, y1 = 0, x2 = 0.175, y2 = 1;
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sampleX = (u: number): number => ((ax * u + bx) * u + cx) * u;
  const sampleY = (u: number): number => ((ay * u + by) * u + cy) * u;
  const solveX = (x: number): number => {
    let u = x;
    for (let i = 0; i < 8; i++) {
      const xEst = sampleX(u) - x;
      const d = (3 * ax * u + 2 * bx) * u + cx;
      if (Math.abs(d) < 1e-6) break;
      u -= xEst / d;
    }
    return u;
  };
  return sampleY(solveX(Math.min(1, Math.max(0, t))));
}
import { isPrivateChat, isAgentDm, dmGroupOf, parseDmId } from '../store/chat';
import { PLATFORM_PROJECT_ID } from '../store/avatar';
import SystemLine from './SystemLine';
import ApprovalCard from './ApprovalCard';
import { itemKeyOf } from '../store/state';
import { buildRowIndex, HeightStore, computeWindow, shouldRenderRow, type WindowRange } from './virtualList';
import { loadSkeleton, saveSkeleton } from '../store/skeletonCache';   // [v1.0.23.6] 骨架持久化
import { useFavoritesStore } from '../store/favorites';
import {
  openAgentMenu, openMenu, toast, confirmModal, openForwardPicker, type MenuEntry,
} from './ContextMenu';
import {
  IconCopy, IconForward, IconStar,
  IconCheckbox, IconQuote, IconTrash,
} from './icons';
import DirectoryRecoveryCard from './DirectoryRecoveryCard';
import { useDirectoryEntry } from '../store/directoryRecovery';
import { useRecordsStore } from '../store/records';
import { useTokenUsageStore } from '../store/tokenUsage';
import { shouldShowDivider, formatDividerLabel } from '../utils/messageTime';

function senderKey(it: Item | undefined): string | null {
  if (!it) return null;
  if (it.kind === 'user') return 'me';
  if (it.kind === 'agent') return 'agent:' + it.agentId;
  return null;
}

/*
 * [v0.10b Bug2] 分组必须认「屏幕上真的画出来的邻居」，而不是 items 数组里的邻居。
 *
 *   空的、且不在流的 agent 气泡会被下面 `if (!it.text) return null` 过滤掉——
 *   可它**仍然占着 items 里的一个下标**。如果拿它当「上一条」来算 grouped，
 *   就会把真正的上一条（比如一张审批卡）挡在后面：卡片本该打断分组，结果
 *   卡片下面那句项目经理消息以为自己接着上一条项目经理气泡（那个幽灵）在说，于是被判「同组」
 *   → MessageBubble 在 grouped 时不画头像那一行 → 「拒绝审批后项目经理跟进消息没头像」。
 *
 *   所以 grouped/tail 都改成跳过这些不渲染的幽灵气泡，只认真正的可见邻居。
 */
/** [v0.38] 这条消息的时间（毫秒）——只有 user/agent 气泡带 ts，其余（系统/审批）无。 */
function msOf(it: Item | undefined): number | null {
  if (!it) return null;
  if (it.kind === 'user' || it.kind === 'agent') {
    return typeof it.ts === 'number' ? it.ts : null;
  }
  return null;
}

/**
 * [v0.5] 屏幕上这个人叫什么、长什么脸——**一处判定**。
 *
 * 三个 bug 都出在「各算各的」上：
 *   #2 审批卡里没有头像 · #3 流式期间头像是文字 · #5 所有项目经理一张脸
 * 现在气泡、流式、审批卡、花名册全都问 avatar.ts 的 faceFor()，
 * 而且**头像永远兜得住**：花名册里没有这个人（流式刚开始、知知没花名册），
 * 也照样按 id 派生出一张脸，绝不退化成文字。
 */
function faceOf(
  members: Member[], agentId: string, projectId: string, projectName: string,
): AgentFace {
  const f = faceFor(agentId, projectId, projectName);
  const m = members.find((x) => x.id === agentId);

  /*
   * [v0.7 #3] 项目经理的角色副标题：**不给**。
   *
   *   他的名字已经是「官网改版 · 项目经理」了（faceFor 给的），再挂一个 role「项目经理」，
   *   气泡上就是「官网改版 · 项目经理 · 项目经理」。这里从源头把 role 留空，
   *   MessageBubble 那边还有一道兜底（名字里已含 role 就不拼）——两道锁，
   *   哪一边先到都不会重复。
   */
  const coordinator = isCoordinator(agentId);

  if (m) {
    return {
      name: f.name ?? memberNameLabel(m.id, m.display.name),
      role: coordinator ? '' : roleLabel(m.display.role),
      glyph: m.display.glyph,
      pal: m.display.pal,
      avatarUrl: m.display.avatarUrl ?? f.avatarUrl,   // ★ 兜底：绝不给出 undefined
    };
  }
  return {
    name: f.name ?? agentId,
    role: coordinator ? '' : 'Agent',
    glyph: (f.name ?? agentId).charAt(0) || '?',
    pal: 'av-d',
    avatarUrl: f.avatarUrl,
  };
}

export interface ChatSearchJump {
  projectId: string;
  itemKey: string;
  requestId: number;
}

export interface ChatStreamProps {
  /** [v1.0.23.5] 会话视图常驻内存：每个实例负责一个固定会话，不再跟随 activeProjectId。 */
  projectId: string;
  /** [v1.0.24.6-P0] 活动态守卫：false = 隐藏会话「停摆」——停 RO/rAF/动画/倒计时/贴底跟随。
   *  只停循环不停挂载：切回时行高缓存/滚动位置原样恢复（active 变 true 即唤醒）。 */
  active: boolean;
  rosterOpen: boolean;
  onToggleRoster: () => void;
  searchJump?: ChatSearchJump | null;
  onSearchJumpDone?: (requestId: number) => void;
}

export const ChatStream: React.FC<ChatStreamProps> = ({
  projectId,
  active = true,
  rosterOpen, onToggleRoster, searchJump = null, onSearchJumpDone,
}) => {
  const { t } = useCachedT();
  // [v1.0.23.5] per-session 订阅：selector 由 useMemo 按 projectId 缓存（引用稳定，不触发多余重渲染）；
  //   immer 引用隔离保证只有本会话数据变化才重渲染。
  const items = useKnoweStore(useMemo(() => makeSelectItems(projectId), [projectId]));
  // [v1.0.23.4] 渲染调度：消息列表渲染标记为可延迟/可中断——点击切群时
  //   头部/列表（轻量订阅）立即响应，消息区渲染让出主线程、空闲分片完成。
  const deferredItems = useDeferredValue(items);

  const members = useKnoweStore(useMemo(() => makeSelectMembers(projectId), [projectId]));
  const name = useKnoweStore(useMemo(() => makeSelectConvName(projectId), [projectId]));
  const banner = useKnoweStore(useMemo(() => makeSelectBanner(projectId), [projectId]));

  /* ═══════════════ [v1.0.23.4] 消息列表虚拟化 ═══════════════
   * 行索引（幽灵 + 可见邻居预计算）→ 高度缓存（估算→实测校正）→ 窗口渲染（视口±overscan）。
   * 详见 Logs/v1.0.23.4-消息列表虚拟化/架构设计.md。 */
  const rows = useMemo(() => buildRowIndex(deferredItems, {
    keyOf: (it, i) => itemKeyOf(it as Item, i),
    reactKeyOf: (it, i) => {
      const x = it as Item;
      return x.kind === 'user' ? (x.cmid || `i${i}`)
        : x.kind === 'approval' ? (x.cardId || `i${i}`)
        : `i${i}`;
    },
  }), [deferredItems]);

  const heightStoreRef = useRef<HeightStore | null>(null);
  if (!heightStoreRef.current) heightStoreRef.current = new HeightStore();
  const heightStore: HeightStore = heightStoreRef.current!;

  // [v1.0.23.6] 骨架持久化：挂载时注入（行高/滚动原位），运行中防抖导出。
  //   骨架只是加速器：丢了/坏了都只是慢，不是错（可重建原则）。
  const skeletonRef = useRef<{ heights: Record<string, number>; scrollTop: number } | null>(null);
  const skeletonInjectedRef = useRef(false);   // 防止快照重建后重复注入旧行高
  const skeletonDirtyRef = useRef(false);      // 防抖标记
  const skeletonTimerRef = useRef(0);
  const flushSkeleton = (): void => {
    if (!skeletonDirtyRef.current) return;
    skeletonDirtyRef.current = false;
    const el = msgsRef.current;
    saveSkeleton(projectId, {
      heights: Object.fromEntries(heightStore.exportHeights()),
      scrollTop: el ? el.scrollTop : 0,
    });
  };
  // 挂载时读骨架（每会话实例只挂载一次——23.5 会话常驻语义）
  useEffect(() => {
    const sk = loadSkeleton(projectId);
    if (sk) skeletonRef.current = { heights: sk.heights, scrollTop: sk.scrollTop };
    return () => {
      // 卸载/销毁前最终写回一次（防丢最后一次滚动位置）
      flushSkeleton();
      if (skeletonTimerRef.current) window.clearTimeout(skeletonTimerRef.current);
      // [v1.0.24.4-r10] 清理接力动画状态（会话销毁时防泄漏）
      relayInfoRef.current = null;
      relayStartedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const [win, setWin] = useState<WindowRange>({ start: 0, end: -1 });
  const [totalH, setTotalH] = useState(0);
  const winTimerRef = useRef(0);
  // [v1.0.24.4] 平滑滚动动画的 rAF id（非 0 = 滚动动画进行中，贴底 effect 据此防重启）
  const scrollAnimRef = useRef(0);
  // [v1.0.24.4] 帧级贴底跟随的 rAF id（卡片 morph 展开期间每帧贴底，跟随展开速率）
  const followRef = useRef(0);
  // [v1.0.24.4] 用户滚轮介入 → 本张新卡不再自动滚动（下一张新卡重置）
  const suppressScrollRef = useRef(false);
  // [v1.0.24.4] 当前处理中的新卡 ik（换卡时重置 suppressScrollRef）
  const newCardIkRef = useRef<string | null>(null);
  // [v1.0.24.4-r7] 本次渲染含「正在收起的推理气泡」（派卡定格）→ 贴底 effect 据此启动跟随
  const retractAnimRef = useRef(false);
  // [v1.0.24.4] 滚轮锁定窗口结束后的底部补判定时器（fab-bottom 残留修复）
  const wheelCheckTimerRef = useRef(0);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  // [v1.0.24.4-r10/r14] 派卡接力：当前接力动画信息。
  //   检测到「新审批卡的前一个 item 是刚定格的推理气泡」→ 建立接力对，
  //   气泡行（原气泡）收起 + 卡片行（原卡片）展开，两行独立渲染，
  //   relaySync effect 的 rAF 逐帧驱动两行高度并同步 HeightStore（零错位）。
  const relayInfoRef = useRef<{
    bubbleIk: string;      // 气泡行 ik
    bubbleRowIdx: number;  // 气泡行行号（HeightStore measure 用）
    cardIk: string;        // 卡片行 ik
    cardRowIdx: number;    // 卡片行行号（HeightStore measure 用）
    done: boolean;         // 动画是否已播完（避免重复建对）
  } | null>(null);
  // [v1.0.24.4-r14] relaySync 启动锁：relay 建立后首个 effect 帧启动 rAF 驱动，
  //   之后每帧 setTotalH 触发 effect 但 started 已 true → 不重复启动。
  const relayStartedRef = useRef(false);
  const rowElsRef = useRef(new Map<string, HTMLElement>());
  /*
   * [v1.0.23.6] 首次挂载快照：初始消息全部视为「历史」→ vrow 加 no-anim，
   * 抑制 display:none 视图切回时 CSS 进入动画整体重播（msgIn/avatarIn/r-rise
   * 全部从起点播放 = 「被打乱又突然变整齐」）。之后真正新增的消息不受影响。
   */
  const mountSeenRef = useRef<Set<string> | null>(null);
  // [v1.0.24.4] 本次渲染是否含「刚挂载的审批卡」：贴底 effect 据此跳过动画中的
  //   立即滚动（卡片还没撑开，scrollHeight 偏小会滚到卡片中部），改由补滚 timer 落位。
  const newCardAnimRef = useRef(false);
  // [v1.0.24.4] 新行延迟入册：入场动画播完前不许加 no-anim（否则 ResizeObserver
  //   高度校正 / useDeferredValue 的第二次渲染把 isNew 翻成 false → vrow 加回
  //   no-anim → animation:none 掐断正在播的展开动画 → 卡片闪现）。
  //   每个新行独立 1s timer 入册（动画统一 ≤0.8s）：行级 timer 不受后续
  //   streaming 刷新的影响，动画播完即落定为「历史」（滚动重挂载不再重播）。
  const [, forceRender] = useReducer((x: number) => x + 1, 0);
  const settleTimersRef = useRef(new Map<string, number>());
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不设 settle timer（省下每新行 1s timer 的 JS 开销）
    if (!active) return;
    const seen = mountSeenRef.current;
    if (!seen) return;
    const timers = settleTimersRef.current;
    for (const r of rowsRef.current) {
      if (seen.has(r.ik) || timers.has(r.ik)) continue;
      const t = window.setTimeout(() => {
        timers.delete(r.ik);
        if (!seen.has(r.ik)) { seen.add(r.ik); forceRender(); }
      }, 1000);
      timers.set(r.ik, t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, active]);

  const roRef = useRef<ResizeObserver | null>(null);

  // [v1.0.24.4] 自定义平滑滚动：与 morph 动画同曲线 cubic-bezier(0.77,0,0.175,1)，
  //   0.5s 慢→快→慢滑到目标位置（浏览器原生 smooth 是平台曲线且时长不可控，达不到要求）。
  const bezierEase = useMemo(() => {
    const x1 = 0.77, y1 = 0, x2 = 0.175, y2 = 1;
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sampleX = (t: number): number => ((ax * t + bx) * t + cx) * t;
    const sampleY = (t: number): number => ((ay * t + by) * t + cy) * t;
    const solveX = (x: number): number => {
      let t = x;
      for (let i = 0; i < 8; i++) {
        const xEst = sampleX(t) - x;
        const d = (3 * ax * t + 2 * bx) * t + cx;
        if (Math.abs(d) < 1e-6) break;
        t -= xEst / d;
      }
      return t;
    };
    return (t: number): number => sampleY(solveX(Math.min(1, Math.max(0, t))));
  }, []);

  // [v1.0.24.4-r7] 帧级贴底跟随：内容持续变化（气泡收起塌缩 / 卡片 morph 展开 / 流式消息）
  //   期间，每帧把视口钉在最新最下方——scrollTop 跟随变化速率被同步推下去（微信贴底质感）。
  //   不依赖贴底 effect 的 totalH 守卫（动画中 totalH 滞后会挡住滚动）。
  //   停止条件：内容稳定 300ms（或总时长超 5s 兜底）→ 交回常规贴底逻辑。
  const startFollow = useCallback((el: HTMLElement): void => {
    if (followRef.current) return;
    const t0 = performance.now();
    let lastH = el.scrollHeight;
    let idleSince = t0;
    const loop = (now: number): void => {
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 2) {
        el.scrollTop = el.scrollHeight;
      }
      const h = el.scrollHeight;
      if (h !== lastH) { lastH = h; idleSince = now; }
      if (now - idleSince < 300 && now - t0 < 5000) followRef.current = requestAnimationFrame(loop);
      else followRef.current = 0;
    };
    followRef.current = requestAnimationFrame(loop);
  }, []);

  // [v1.0.24.4] 不在底部时：0.5s 慢→快→慢平滑滑到「最新最下方」。
  //   目标每帧重算（= 实时底部）——卡片展开/后续消息让 scrollHeight 持续上涨，
  //   滚动过程自动跟随，不会停在动画中的缩水位置。到位后若已贴底 → 转帧级跟随。
  const smoothScrollBottom = useCallback((el: HTMLElement): void => {
    if (scrollAnimRef.current) { cancelAnimationFrame(scrollAnimRef.current); scrollAnimRef.current = 0; }
    const startTop = el.scrollTop;
    const t0 = performance.now();
    const step = (now: number): void => {
      const p = Math.min(1, (now - t0) / 500);
      const target = el.scrollHeight - el.clientHeight;
      el.scrollTop = startTop + (target - startTop) * bezierEase(p);
      if (p < 1) scrollAnimRef.current = requestAnimationFrame(step);
      else {
        scrollAnimRef.current = 0;
        // 到位后仍在底部（内容还在展开）→ 帧级跟随兜住剩余动画时间
        if (atBottomRef.current) startFollow(el);
      }
    };
    scrollAnimRef.current = requestAnimationFrame(step);
  }, [bezierEase, startFollow]);

  // 行数变化：高度缓存 resize（幽灵行 0 占位），窗口按当前 scrollTop 重算
  // [v1.0.23.5] 会话常驻：实例与会话一对一，不再有「切群」——rows 变化只可能是
  //   本会话数据变化（流式/快照），走 resize 保留实测高度，窗口随滚动位置重算。
  // [v1.0.23.6] 首次有数据且骨架存在 → importHeights 注入实测行高（免重估），
  //   并在同一批次恢复滚动位置——行高是准的，一次到位，无中间帧。
  useEffect(() => {
    if (!skeletonInjectedRef.current && rows.length > 0) {
      skeletonInjectedRef.current = true;
      const sk = skeletonRef.current;
      if (sk && Object.keys(sk.heights).length > 0) {
        heightStore.importHeights(new Map(Object.entries(sk.heights)), rows);
      } else {
        heightStore.resize(rows);
      }
      setTotalH(heightStore.totalHeight);
      const el = msgsRef.current;
      if (el) {
        if (sk && sk.scrollTop > 0) {
          el.scrollTop = Math.min(sk.scrollTop, el.scrollHeight - el.clientHeight);
        }
        setWin(computeWindow(el.scrollTop, el.clientHeight, heightStore, rows));
      }
      return;
    }
    heightStore.resize(rows);
    setTotalH(heightStore.totalHeight);
    const el = msgsRef.current;
    if (el) {
      setWin(computeWindow(el.scrollTop, el.clientHeight, heightStore, rows));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  // 统一一个 ResizeObserver 观察所有窗口行：实测高度 → 增量校正前缀和
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不建 RO（恢复时由下方 [win,rows,active] effect 重建 + 全量测量）
    if (!active) return;
    const ro = new ResizeObserver((entries) => {
      let changed = false;
      for (const en of entries) {
        const el = en.target as HTMLElement;
        const ik = el.dataset.ik ?? '';
        const idx = parseInt(el.dataset.index ?? '-1', 10);
        // [v1.0.24.4-r11] relay 动画期间跳过：气泡行 vrow 的 offsetHeight =
        //   RelayMorph 总高（气泡层 bh + 卡片层 ch），RO 写入气泡行会让它与
        //   onFrame 每帧竞争 → 总高度 ±卡片高 交替跳变（白色空白/卡片跳动根因）。
        //   这两行由 rAF 独占驱动，落定后 relayInfo 清空、RO 恢复测量。
        const ri = relayInfoRef.current;
        if (ri && (ik === ri.bubbleIk || ri.cardIk === ik)) continue;
        // [v1.0.24.4-r16] 幽灵行永不测量：幽灵行不渲染，任何实测值必为过期污染。
        //   实锤竞态：relay 落定瞬间气泡行 inline 清空 → 高度弹回自然值，RO 在
        //   relayInfo 已清空、元素未卸载的窗口把 317 写回前缀和 → 卡片上方永久空白。
        const rowAt = rowsRef.current[idx];
        if (rowAt && rowAt.ghost) continue;
        if (ik && idx >= 0) {
          heightStoreRef.current!.measure(ik, idx, el.offsetHeight);
          changed = true;
        }
      }
      if (changed) {
        setTotalH(heightStoreRef.current!.totalHeight);
        // [v1.0.23.6] 实测行高变化 → 防抖导出骨架（1s 合并，落 localStorage）
        if (!skeletonDirtyRef.current) {
          skeletonDirtyRef.current = true;
          if (skeletonTimerRef.current) window.clearTimeout(skeletonTimerRef.current);
          skeletonTimerRef.current = window.setTimeout(flushSkeleton, 1000);
        }
      }
    });
    roRef.current = ro;
    return () => ro.disconnect();
  }, [active]);

  // 窗口行变化 → 重新 observe + 主动初始测量（RO 只在尺寸变化时回调，首次挂载需手动测一次）
  useEffect(() => {
    const ro = roRef.current;
    if (!active || !ro) return;
    ro.disconnect();
    let changed = false;
    for (const [ik, el] of rowElsRef.current) {
      ro.observe(el);
      const idx = parseInt(el.dataset.index ?? '-1', 10);
      // [v1.0.24.4-r11] 同 RO 回调：relay 行跳过主动测量（rAF 独占，避免竞争跳变）
      const ri = relayInfoRef.current;
      if (ri && (ik === ri.bubbleIk || ik === ri.cardIk)) continue;
      // [v1.0.24.4-r16] 同 RO 回调：幽灵行永不测量（任何实测值必为过期污染）
      const rowAt = rowsRef.current[idx];
      if (rowAt && rowAt.ghost) continue;
      if (ik && idx >= 0) {
        heightStoreRef.current!.measure(ik, idx, el.offsetHeight);
        changed = true;
      }
    }
    if (changed) setTotalH(heightStoreRef.current!.totalHeight);
  }, [win, rows, active]);

  // [v1.0.24.4-r14] 派卡接力同步器：relay 建立后启动 rAF，逐帧驱动气泡行（H→0）与
  //   卡片行（0→C）高度 + 同步 HeightStore。useLayoutEffect 在浏览器绘制前执行 →
  //   首帧「算高度→写 DOM→measure→布局」完成后再绘制，无中间帧闪烁/错位（零错位）。
  //   依赖 [rows]：relay 建立帧（新审批卡加入 items → rows 重建）提交后启动
  //   （relayStartedRef 防重入；rAF 循环自驱，不依赖 effect 重跑）。
  useLayoutEffect(() => {
    const ri = relayInfoRef.current;
    if (!ri || ri.done) return;
    const els = rowElsRef.current;
    // [v1.0.24.7-P0-2-防线4] 兜底**先注册**（在任何提前 return 之前）：relay 建立后
    //   1.5s 无论 rAF 是否启动，强制落定。旧实现把 setTimeout 放在 relayStartedRef
    //   检查之后——切群（active 翻转）建立的 relay 若 effect 因依赖 [rows] 不重跑，
    //   rAF 不启动且兜底也不注册 → relayInfo 永久挂着 → 渲染循环 fallback 用幽灵行
    //   0 高算 top → 卡片与气泡同 top 重叠（多群并发逐个切群 4 群全中实锤）。
    //   动画正常播完时 relayInfo 已清空，定时器无害（下面对 null 早退）。清理函数
    //   不取消：本 effect 因 rows 变化重跑时旧定时器由 closure 持有，若取消可能把
    //   新动画的兜底误杀。
    window.setTimeout(() => {
      const ri2 = relayInfoRef.current;
      if (!ri2 || ri2.done) { relayStartedRef.current = false; return; }
      // 同 rAF 落定路径：measure 终值 + 清 relay 状态 + 恢复 overflow/top 交还 React
      const bEl2 = els.get(ri2.bubbleIk);
      const cEl2 = els.get(ri2.cardIk);
      if (bEl2) { bEl2.style.overflow = ''; }
      if (cEl2) {
        cEl2.style.overflow = '';
        cEl2.style.height = '';
        cEl2.style.top = '';
      }
      heightStore.measure(ri2.bubbleIk, ri2.bubbleRowIdx, 0);
      heightStore.measure(ri2.cardIk, ri2.cardRowIdx, bEl2 ? bEl2.offsetHeight : cEl2 ? cEl2.scrollHeight : 0);
      setTotalH(heightStore.totalHeight);
      ri2.done = true;
      relayInfoRef.current = null;
      relayStartedRef.current = false;
      heightStore.unprotect(ri2.bubbleIk);
      heightStore.unprotect(ri2.cardIk);
      forceRender();
    }, 1500);
    // [v1.0.24.4-r14] relayStartedRef 防重入：rAF 已在跑 → 不重复启动（动画自驱）。
    if (relayStartedRef.current) return;
    // [v1.0.24.7-P0-1-防线2] 启动失败回滚（含 active 翻转路径）：relay 对已建立（渲染阶段
    //   active=true 才建），但本 effect 执行时已不满足启动条件（active 变 false / 元素未
    //   就绪）→ 不再静默放弃（旧行为 = 永久挂起：relayInfo 永远指向这两行 → 渲染循环不写
    //   top → 卡片叠到消息流最上方）。回滚 = 清空 relayInfo + 解除保护 + 强制重渲染 →
    //   React 恢复正常写 top（位置保底正确，只是没有动画）。
    //   ⚠️ [v1.0.24.6-P0] 原「if (!active) return」只停动画不停状态——relay 建立后切走群，
    //   状态永远挂着，切回也不恢复（effect 依赖 [rows] 不含 active）。这里统一走回滚。
    //   ⚠️ [v1.0.24.7-P0-2] 依赖加 active：切群（active 翻转）时本 effect 必须重跑——
    //   切回群时 relay 在渲染阶段重建（active 变 true），若依赖只有 [rows]（引用没变）
    //   effect 不重跑 → rAF 永不启动 → 上面的兜底是最后防线（1.5s 落定，无动画但有
    //   正确位置）。依赖加 active 后切回立即启动 rAF → 动画照常。
    if (!active) {
      relayStartedRef.current = false;
      relayInfoRef.current = null;
      heightStore.unprotect(ri.bubbleIk);
      heightStore.unprotect(ri.cardIk);
      forceRender();
      return;
    }
    relayStartedRef.current = true;
    const bubbleEl = els.get(ri.bubbleIk);
    const cardEl = els.get(ri.cardIk);
    // [v1.0.24.7-P0-1-防线2] 元素未就绪同样回滚（不再静默 return）
    if (!bubbleEl || !cardEl) {
      relayStartedRef.current = false;
      relayInfoRef.current = null;
      heightStore.unprotect(ri.bubbleIk);
      heightStore.unprotect(ri.cardIk);
      forceRender();
      return;
    }
    // [v1.0.24.7-P0-2] 落定超时兜底已提前到 effect 开头注册（防线4）——旧防线3 删除。
    // 初始高度：气泡行当前实际高（推理面板展开态）；卡片行完整内容高（scrollHeight
    //   不受 vrow 高度压缩影响——内容已渲染，只是被 overflow hidden 裁切）。
    const H = bubbleEl.offsetHeight || 64;
    const C = cardEl.scrollHeight || 400;
    // 首帧预置（无跳变）：气泡保持 H、卡片 0；top 同步（渲染循环不写 relay 行 top）
    bubbleEl.style.overflow = 'hidden';
    cardEl.style.overflow = 'hidden';
    bubbleEl.style.height = `${H}px`;
    cardEl.style.height = '0px';
    heightStore.measure(ri.bubbleIk, ri.bubbleRowIdx, H);
    heightStore.measure(ri.cardIk, ri.cardRowIdx, 0);
    // [v1.0.24.6-P3] 气泡行 top 动画期间恒定（前缀和只受前面行影响，前面行动画中
    //   不动）→ 只写一次，loop 不再重复写（每帧省 1 次 style 写）。
    bubbleEl.style.top = `${heightStore.topOf(ri.bubbleRowIdx)}px`;
    cardEl.style.top = `${heightStore.topOf(ri.cardRowIdx)}px`;
    setTotalH(heightStore.totalHeight);
    const T0 = performance.now();
    const TOTAL = 1.0;   // 收起 1s；展开 0.8s 并行（用户定参）
    const loop = (now: number): void => {
      const t = Math.min((now - T0) / 1000, TOTAL);
      const bt = Math.min(t / 1.0, 1);
      const ct = Math.min(t / 0.8, 1);
      const bh = H * (1 - easeCubic(bt));
      const ch = C * easeCubic(ct);
      // 同帧「算高→measure（更新前缀和）→写 top」——绘制时 DOM 高度与位置必然一致
      heightStore.measure(ri.bubbleIk, ri.bubbleRowIdx, bh);
      heightStore.measure(ri.cardIk, ri.cardRowIdx, ch);
      bubbleEl.style.height = `${bh}px`;
      cardEl.style.height = `${ch}px`;
      // [v1.0.24.4-r14] top 由 rAF 独占写（React 渲染滞后一帧 → 瞬态重叠的根因）
      // [v1.0.24.6-P3] 气泡 top 恒定不再重写；卡片 top 每帧跟（前缀和随 bh 变）
      cardEl.style.top = `${heightStore.topOf(ri.cardRowIdx)}px`;
      setTotalH(heightStore.totalHeight);
      if (t < TOTAL) {
        requestAnimationFrame(loop);
      } else {
        // 落定：气泡行 0 高、卡片行完整高度，恢复 overflow；top 交回 React 渲染循环
        heightStore.measure(ri.bubbleIk, ri.bubbleRowIdx, 0);
        heightStore.measure(ri.cardIk, ri.cardRowIdx, C);
        // [v1.0.24.4-r16] 气泡行的 inline **保持 0 高 + hidden 不清除**：这一行马上被
        //   forceRender 卸载（幽灵守卫返回 null）。若在此清 inline，元素高度瞬间弹回自然高
        //   （推理面板 ~317px）→ ResizeObserver 在「relayInfo 已清空、元素尚未卸载」的窗口
        //   触发 → measure(幽灵行, 317) 写回前缀和 → 卡片上方 317px 永久空白
        //   （活体实锤：settle 后 heights[s46146]=317、offsets +317、无 rebuild 纠正，
        //   骨架导出同步固化 317 → 复现稳定）。元素带着 0 高卸载，残留 inline 无害。
        //   卡片行照常清除：它的自然高度就是真实值，RO 实测是校正不是污染。
        cardEl.style.height = '';
        cardEl.style.overflow = '';
        cardEl.style.top = '';
        setTotalH(heightStore.totalHeight);
        ri.done = true;
        relayInfoRef.current = null;
        relayStartedRef.current = false;
        heightStore.unprotect(ri.bubbleIk);
        heightStore.unprotect(ri.cardIk);
        forceRender();
      }
    };
    requestAnimationFrame(loop);
    // [v1.0.24.4-r14] 清理函数**不取消 rAF**：动画期间 rows 可能变化（卡片后的新气泡
    //   streaming 进入 items）→ 本 effect 重跑 → 若在这里 cancel，rAF 被误杀且
    //   relayStartedRef 挡住重启 → 卡片卡在 0 高「出不来」。rAF 循环 1s 后自然结束。
    return () => { /* 自驱动，不取消 */ };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, active]);

  // [v1.0.23.5_2] 窗口缩放节流：rAF 合并 resize 事件，每帧最多重算一次虚拟窗口。
  //   背景：resize 风暴（审计 01）——连续拖拽时原生重排 + React 层重算叠加，
  //   事件积压 → 渲染进程 516% CPU → 系统卡死。rAF 合并保证 React 层每帧只响应一次。
  const resizeRafRef = useRef(0);
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不注册 resize 监听
    if (!active) return;
    const onResize = (): void => {
      if (resizeRafRef.current) return;
      resizeRafRef.current = requestAnimationFrame(() => {
        resizeRafRef.current = 0;
        const el = msgsRef.current;
        if (el) {
          setWin(computeWindow(el.scrollTop, el.clientHeight, heightStoreRef.current!, rowsRef.current));
        }
      });
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      if (resizeRafRef.current) cancelAnimationFrame(resizeRafRef.current);
    };
  }, [active]);

  const scrollToIndex = useCallback((i: number): void => {
    const el = msgsRef.current;
    if (!el) return;
    const y = heightStoreRef.current!.topOf(i);
    el.scrollTo({ top: Math.max(0, y - el.clientHeight / 2), behavior: 'smooth' });
  }, []);

  // [v1.0.23.4] 跳转改造：目标行可能在窗口外（虚拟化已卸载）→ 先滚动窗口，轮询等行渲染后闪烁。
  const flashItemByKey = useCallback((ref: string): boolean => {
    const idx = rowsRef.current.findIndex((r) => r.ik === ref);
    if (idx < 0) return false;
    scrollToIndex(idx);
    let tries = 0;
    const poll = (): void => {
      tries += 1;
      const node = msgsRef.current?.querySelector<HTMLElement>(`.mgroup[data-ik="${ref}"]`);
      if (node) {
        node.classList.remove('flash');
        void node.offsetWidth;
        node.classList.add('flash');
        window.setTimeout(() => node.classList.remove('flash'), 1700);
        return;
      }
      if (tries < 40) window.setTimeout(poll, 50);
    };
    window.setTimeout(poll, 120);
    return true;
  }, [scrollToIndex]);

  const jumpToItemKey = useCallback((ref: string): void => {
    flashItemByKey(ref);
  }, [flashItemByKey]);
  const acknowledgeTransientFrame = useKnoweStore((s) => s.ackTransientFrame);
  const onTransientFramePaint = useCallback((frameId: string): void => {
    if (projectId) acknowledgeTransientFrame(projectId, frameId);
  }, [acknowledgeTransientFrame, projectId]);

  // [v1.0.23.3] 点击四方向卡片 → 自动发送：卡片原文（title）+ 自动 @来源agent
  // [v1.0.23.11] ★ @ 用**花名册权威名**，不用 faceFor 复合显示名：
  //   复合名「测试1 · 项目经理」的后缀会被后端前缀匹配误判——`@测试1 · 项目经理`
  //   里 re.match("测试") 先命中 qa_1 的角色别名「测试」→ 点总管卡片却直达测试 worker。
  //   权威名（coordinator→「项目经理」、worker→花名册 name 如「董然」）是解析器的天然别名，
  //   精确路由。member 查不到（流式刚起、花名册还没这个人）才退回 face.name 兜底。
  // [v1.0.23.13] ★ members 走 ref + 按 agentId 缓存返回函数：
  //   · members 走 ref：selectActiveMembers 内部 orderRosterMembers 在 busy/standby
  //     成员存在时每次返回**新数组**（[...].sort()）——若进 useCallback 依赖，每次成员
  //     状态变化都重建 suggestionSendFor → 新函数引用 → 击穿 MessageBubble 的
  //     React.memo → 整条消息流重渲染 → 切会话/拖窗口卡顿。
  //   · useMemo 持缓存：同一 projectId 下同一 agentId 永远返回**同一个函数引用**，
  //     memo 浅比较通过不重渲染；projectId 变化（切群）才重建缓存。
  const membersRef = useRef(members);
  membersRef.current = members;
  const suggestionSendFor = useMemo(() => {
    const cache = new Map<string, (text: string) => void>();
    return (agentId: string): ((text: string) => void) => {
      let fn = cache.get(agentId);
      if (!fn) {
        fn = (text: string): void => {
          const member = membersRef.current.find((x) => x.id === agentId);
          const name = member?.display.name || agentId;
          const mention = `@${name} `;
          useKnoweStore.getState().sendMessage(`${text}\n${mention}`, projectId || '', undefined);
        };
        cache.set(agentId, fn);
      }
      return fn;
    };
  }, [projectId]);

  /*
   * [v0.40.0] 右键菜单一族的状态（多选）。
   *   selectedKeys 是小对象表，immer 只在真的变化时才换引用，
   *   订阅它不会把消息流拖进无谓的重渲染。
   */
  const selecting = useKnoweStore((s) => s.selecting);
  const selectedKeys = useKnoweStore((s) => s.selectedKeys);

  // [v0.38] 聊天记录抽屉开合：开时覆盖整张 .chat-card。
  // [v1.0.23.5] recordsOpen/tokenPanelOpen 为全局覆盖层开关（覆盖层已上提 App 层）；
  //   头部按钮仍按各自会话判断显隐，故保留这两个订阅。
  const recordsOpen = useRecordsStore((s) => s.open);
  const tokenPanelOpen = useTokenUsageStore((s) => s.open);

  // [v1.0.23.5] 聊天记录 = 本实例负责的会话本身（projectId 由 props 固定）
  const recordsIsGroup = !isPrivateChat(projectId) && projectId !== PLATFORM_PROJECT_ID;

  // [v0.13 模块A] 目录恢复卡片渲染在 .msgs 末尾，但它来自 directoryRecovery store、不属于 items。
  //   订阅它，好让下面的自动滚底把「卡片出现 / 收起 / 重开 / 消失」也当成一次内容变化。
  const dirEntry = useDirectoryEntry(projectId);

  const msgsRef = useRef<HTMLDivElement>(null);
  // [v1.0.24.6-P0] active 的同步 ref：贴底 effect 用它做守卫（依赖数组不能含 active，
  //   否则切群重跑 effect 把 scrollTop 拉回底部，破坏微信式滚动保持）。每次渲染同步。
  const activeRef = useRef(active);
  activeRef.current = active;
  const [scrolled, setScrolled] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  // [v1.0.23.5] 贴底判断用同步 ref：atBottom state 更新是异步的，流式高频
  //   items 更新会在旧闭包里读到过期 true，把用户正在上翻的位置拽回底部
  //   （live 滚动「卡住」的根因）。ref 在 onScroll 里同步写入，effect 读它。
  const atBottomRef = useRef(true);
  // [v1.0.23.6-r3] 最近一次用户滚轮时间戳：滚轮 = 用户介入的权威信号，
  //   300ms 内 onScroll 的 bottom 判定不覆盖退出跟随状态（防流式增长抵消滚动量）。
  const lastWheelRef = useRef(0);
  const [bannerHidden, setBannerHidden] = useState(false);
  // [v1.0.23.4] 群聊中途添加 Agent 员工：Popover 锚点（按钮点击处坐标，null=关闭）
  const [addAgentsAnchor, setAddAgentsAnchor] = useState<{ x: number; y: number } | null>(null);

  // ── [v1.0.19.4] 拖拽本地文件进聊天区上传（与「添加附件」按钮同一条通道）──
  const [dragging, setDragging] = useState(false);
  const dragDepthRef = useRef(0);
  const addAttachments = useKnoweStore((s) => s.addAttachments);
  const dragBridge = typeof window !== 'undefined'
    ? (window as unknown as {
        knowe?: { signDroppedFiles?: (paths: string[]) => Promise<Array<Record<string, unknown>>> };
      }).knowe
    : undefined;
  const canDropFiles = Boolean(dragBridge?.signDroppedFiles);
  // 只认**文件**拖拽：拖文字/链接（types 里没有 'Files'）一律不接管，浏览器默认行为不受影响。
  const dragHasFiles = (e: React.DragEvent): boolean =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files');
  const onZoneDragEnter = (e: React.DragEvent): void => {
    if (!canDropFiles || !projectId || !dragHasFiles(e)) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setDragging(true);
  };
  const onZoneDragOver = (e: React.DragEvent): void => {
    if (!canDropFiles || !projectId || !dragHasFiles(e)) return;
    e.preventDefault();               // 阻止浏览器把窗口导航去打开这个文件
    e.dataTransfer.dropEffect = 'copy';
  };
  const onZoneDragLeave = (e: React.DragEvent): void => {
    if (!dragHasFiles(e)) return;
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragging(false);   // 只有真正离开整片区域才收起遮罩
  };
  const onZoneDrop = (e: React.DragEvent): void => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    if (!canDropFiles || !projectId) return;
    const paths = Array.from(e.dataTransfer.files ?? [])
      .map((f) => (f as unknown as { path?: string }).path)
      .filter((p): p is string => Boolean(p));
    if (paths.length === 0) return;
    void (async () => {
      try {
        const picks = await dragBridge!.signDroppedFiles!(paths);
        if (Array.isArray(picks) && picks.length) {
          addAttachments(projectId, picks as never);
          toast(t('composer.multimodalWarn'));
        }
      } catch {
        toast(t('chat.stream.dragAddFailed'));
      }
    })();
  };

  // 新 banner 出现（引用变化）→ 横幅重新可见；手动关闭后同一条 banner 保持关闭
  // [v1.0.23.5] 原依赖 projectId（切群重置）——会话常驻后 projectId 固定，改为依赖 banner 值。
  useEffect(() => { setBannerHidden(false); }, [banner]);

  // 新消息 / 目录卡片状态变化 → 贴底（只在本来就在底部时；翻历史时不许被拽走）
  //   [v0.13 模块A] 依赖里加 dirEntry：卡片出现/收起/重开时 items 没变，但引用变了 → 触发滚底。
  //   [v1.0.23.4] 用 deferredItems：渲染完成后才滚底；totalH 跟随（虚拟行实测高度
  //     校正后总高变化，贴底时保持吸底）。
  //   [v1.0.24.4] 新审批卡出现且用户贴底：不立即跳（卡片在展开，scrollHeight 偏小），
  //     改为 0.5s 慢→快→慢平滑滚动到卡片主体居中——用户全程看得到 morph 展开，
  //     到位时（0.5s）卡片展开约 6 成、继续长到 0.8s 正好居中。普通消息维持立即贴底。
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不贴底、不滚动。用 activeRef 守卫而非依赖数组——
    //   依赖里不能有 active（切群时 effect 重跑会把 scrollTop 拉回底部，破坏微信式滚动保持）。
    //   隐藏期间新消息触发重跑 → 读 ref 为 false 直接跳过；切回后新消息 → ref 为 true 正常贴底。
    if (!activeRef.current) return;
    const el = msgsRef.current;
    if (!el) return;
    // [v1.0.24.4-r7] 新卡 / 收起气泡分支**不经过 atBottom/totalH 守卫**：卡片挂载瞬间虚拟列表
    //   高度（vlist）还没更新（RO 测量滞后），守卫会挡住跟随启动 → 卡片展开期间
    //   视口不跟随（gap 越拉越大）。帧级跟随直接用真实 scrollHeight 贴底，无需守卫。
    // [v1.0.24.4-r14] ★ 此分支原被下方「!atBottomRef.current → return」提前挡掉——
    //   视口不在底部（用户翻过历史 / 上次动画残留）时 relay 动画完全不引导视口，
    //   卡片在视口外展开 → 观感「卡片出不来」。提到守卫之前：动画期间视口
    //   贴底则帧级跟随、不贴底则平滑滑到底（引导用户看卡片）。
    if (newCardAnimRef.current || retractAnimRef.current) {
      newCardAnimRef.current = false;
      retractAnimRef.current = false;
      // 用户滚轮介入过（本张卡）或滚动动画/跟随已在跑 → 不重复启动
      if (!suppressScrollRef.current && !scrollAnimRef.current && !followRef.current) {
        // [v1.0.24.4-r6/r7] 新卡出现 / 推理气泡收起：视角的目标是「最新最下方」——
        //   已在底部 → 帧级跟随（内容变化把视口推到最新底部，全程无跳变）；
        //   不在底部 → 0.5s 慢→快→慢平滑滑到底部，到位后转帧级跟随。
        if (atBottomRef.current) {
          startFollow(el);
        } else {
          smoothScrollBottom(el);
        }
      }
      return;
    }
    if (!atBottomRef.current) return;
    // vlist 未就绪（totalH 中间态）不贴底：否则 scrollHeight 偏小，
    //   scrollTop = scrollHeight - clientHeight 会算出错误位置（如 0）。
    const vl = el.querySelector('.vlist') as HTMLElement | null;
    if (!vl) return;
    const vlH = parseFloat(vl.style.height) || 0;
    if (vlH < heightStoreRef.current!.totalHeight - 2) return;
    el.scrollTop = el.scrollHeight;
  }, [deferredItems, dirEntry, totalH, smoothScrollBottom, startFollow]);

  // [v0.38.3 #3] 「跳转到消息出处」：记录里右键选跳转 → 抽屉关 → 滚到该消息 → 短暂高亮。
  //   [v1.0.23.4] 虚拟化后行可能不在 DOM：按 itemKeyOf 定位滚动，轮询等行渲染后闪烁。
  const jumpSeq = useRecordsStore((s) => s.jumpSeq);
  useEffect(() => {
    if (jumpSeq == null) return;
    const clearJump = useRecordsStore.getState().clearJump;
    const idx = rowsRef.current.findIndex((r) => r.ik === `s${jumpSeq}`);
    if (idx >= 0) {
      scrollToIndex(idx);
      let tries = 0;
      const poll = (): void => {
        tries += 1;
        const target = msgsRef.current?.querySelector<HTMLElement>(`.mgroup[data-seq="${jumpSeq}"]`);
        if (target) {
          target.classList.remove('msg-jump-hit');
          void target.offsetWidth;
          target.classList.add('msg-jump-hit');
          window.setTimeout(() => target.classList.remove('msg-jump-hit'), 1800);
          clearJump();
          return;
        }
        if (tries < 40) window.setTimeout(poll, 50);
        else clearJump();
      };
      window.setTimeout(poll, 150);
    } else {
      clearJump();
    }
  }, [jumpSeq, scrollToIndex]);

  // 全局搜索跨会话落点：目标会话及消息 DOM 就绪后，复用同一把 itemKey 定位钥匙。
  useEffect(() => {
    if (!searchJump || searchJump.projectId !== projectId) return;
    if (flashItemByKey(searchJump.itemKey)) onSearchJumpDone?.(searchJump.requestId);
  }, [flashItemByKey, items, onSearchJumpDone, projectId, searchJump]);

  /*
   * ═══ [v0.40.0] 消息右键菜单（README §3.2 的七项，用户/Agent 完全一样） ═══
   *
   * 触发面 = 整个 .mgroup：文本气泡、文件卡片、图片、视频都从这里冒泡进来（§3.1）。
   * 「复制」的取文规则（§3.2-1）：右键落点若在某张文件卡上（data-fc-name），
   * 复制那张卡的文件名；否则取消息正文；没有正文再取首个文件名；都没有 → 空串。
   */
  const msgMenu = (e: React.MouseEvent, it: Item, index: number): void => {
    if (it.kind !== 'user' && it.kind !== 'agent') return;
    e.preventDefault();
    const pid = projectId || '';
    const ik = itemKeyOf(it, index);
    const isUser = it.kind === 'user';
    const face = isUser ? null : faceCache(it.agentId);
    const senderName = isUser ? '我' : (face?.name ?? 'Agent');

    const fcName = (e.target as HTMLElement | null)
      ?.closest?.('[data-fc-name]')?.getAttribute('data-fc-name');
    const files = it.kind === 'agent' ? (it.files ?? []) : [];
    const copyText = fcName || it.text || files[0]?.name || '';
    // 右键落点的具体文件（文件卡/图片/视频收藏 → 收藏这张文件，而非整条消息文字）。
    const clickedFile = fcName ? files.find((f) => f.name === fcName) : undefined;

    // [v1.0.23.1] 这条消息的「待转发内容」：文本 + 文件 + 是否 markdown（Agent 富文本）+ 源。
    //   来源群名：当前会话可能是私聊（dm:），私聊取父群名（与 favorites.projectLabelOf 同口径）。
    const srcConv = useKnoweStore.getState().convs[pid];
    const forwardItem = (): ForwardItem => ({
      text: it.text,
      files: files.length ? files : undefined,
      markdown: it.kind === 'agent',
      sourceName: senderName,
      sourceProjectName: srcConv?.parentProjectName || srcConv?.projectName,
      sourceRef: { projectId: pid, itemKey: ik },
    });

    const st = useKnoweStore.getState();
    const items: MenuEntry[] = [
      {
        icon: <IconCopy />, label: t('chat.stream.13'), key: '⌘C',
        onClick: () => {
          void navigator.clipboard?.writeText(copyText);
          toast(t('common.toastCopied'));
        },
      },
      { icon: <IconForward />, label: t('chat.stream.06'), onClick: () => openForwardPicker([forwardItem()]) },
      {
        icon: <IconStar />, label: t('common.09'),
        onClick: () => {
          const conv = useKnoweStore.getState().convs[pid]
            ?? { projectId: pid, projectName: name, items: [], members: [], banner: null, draft: '', unread: 0, };
          const isGroup = !priv && pid !== PLATFORM_PROJECT_ID;
          // #7 Agent 文字气泡（非文件收藏）→ 默认标题「群名 - Agent名 · 职能 - 发言」+ 保留 markdown。
          //     私聊 Agent（isAgentDm）时窗口 projectId 是私聊 id，priv=true，旧逻辑会误落到「知知」；
          //     此处优先取所属群的名字（dmGroup→groupName），才能得到「测试1 - 邓青恒 · 产品 - 发言」。
          const titleParts = (!clickedFile && it.kind === 'agent' && face)
            ? {
              groupLabel: dmGroup ? groupName : (priv ? t('common.10') : (name || pid)),
              agentName: face.name,
              role: face.role,
            }
            : undefined;
          useFavoritesStore.getState().addFromMessage({
            conv,
            item: it,
            sourceName: senderName,
            pal: isUser ? 'av-a' : (face?.pal ?? 'av-d'),
            isGroup,
            file: clickedFile,           // #6 右键落在文件卡上 → 收藏这张文件
            titleParts,
          });
          toast(t('common.toastFavorited'));
        },
      },
      { icon: <IconCheckbox />, label: t('chat.stream.14'), onClick: () => st.enterSelect(ik) },
      {
        icon: <IconQuote />, label: t('chat.stream.18'),
        onClick: () => st.setQuote({ name: senderName, text: it.text, projectId: pid, itemKey: ik }),
      },
      '---',
      {
        icon: <IconTrash />, danger: true, label: t('chat.stream.10'),
        onClick: () => confirmModal({
          title: t('chat.stream.11'),
          body: t('chat.stream.08'),
          okLabel: t('chat.stream.10'),
          danger: true,
          onOk: () => { st.removeItemsFromView(pid, [ik]); toast(t('common.toastDeleted')); },
        }),
      },
    ];
    openMenu(items, e.clientX, e.clientY);
  };

  const onWheel = (): void => {
    // [v1.0.23.6-r3] 滚轮事件 = 用户主动介入滚动（权威信号，不看距离判定）：
    //   立即退出流式跟随，流式增长不再能拽回滑块。不 preventDefault——
    //   任务卡片等内部滚动容器（.ap-task/.reasoning-scroll）的滚轮冒泡到此处，
    //   只标记不拦截，它们的内部滚动照常。
    lastWheelRef.current = Date.now();
    atBottomRef.current = false;
    setAtBottom(false);
    // [v1.0.24.4] 用户介入 → 取消进行中的自动平滑滚动 / 帧级跟随；本张新卡不再自动滚动
    if (scrollAnimRef.current) { cancelAnimationFrame(scrollAnimRef.current); scrollAnimRef.current = 0; }
    if (followRef.current) { cancelAnimationFrame(followRef.current); followRef.current = 0; }
    suppressScrollRef.current = true;
    // [v1.0.24.4] 滚轮锁定窗口（300ms）内 onScroll 不更新底部状态；用户停在底部后
    //   无后续滚动事件 → atBottom 卡在 false → fab-bottom 一直显示。窗口结束补判一次。
    if (wheelCheckTimerRef.current) window.clearTimeout(wheelCheckTimerRef.current);
    wheelCheckTimerRef.current = window.setTimeout(() => {
      wheelCheckTimerRef.current = 0;
      const el = msgsRef.current;
      if (!el) return;
      const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 2;
      if (Date.now() - lastWheelRef.current >= 300) {
        atBottomRef.current = bottom;
        setAtBottom(bottom);
      }
    }, 350);
  };

  const onScroll = (): void => {
    const el = msgsRef.current;
    if (!el) return;
    // [v1.0.23.6-r3] 底部判定加 300ms 滚轮锁定：
    //   流式增长时 scrollHeight 每帧上涨，用户滚轮向上 M px 会被内容增长 N px 抵消，
    //   bottom 判定仍 <2 → 误判「在底部」→ 贴底 effect 拽回 → 滑块滑不动。
    //   滚轮是用户介入的权威信号（onWheel 里已强制退出跟随）；此处在滚轮刚结束的
    //   300ms 内不覆盖退出状态，避免「滚了一下又被 bottom 判定拽回」。
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 2;
    if (Date.now() - lastWheelRef.current > 300) {
      atBottomRef.current = bottom;   // [v1.0.23.5] 同步写 ref，贴底 effect 立即读到最新值
      setAtBottom(bottom);
    }
    setScrolled(el.scrollTop > 4);
    // [v1.0.23.6] 滚动位置变化 → 防抖导出骨架（与行高共用 1s 合并定时器）
    if (!skeletonDirtyRef.current) {
      skeletonDirtyRef.current = true;
      if (skeletonTimerRef.current) window.clearTimeout(skeletonTimerRef.current);
      skeletonTimerRef.current = window.setTimeout(flushSkeleton, 1000);
    }
    // [v1.0.23.4] 虚拟窗口重算（setTimeout 节流：Electron 失焦时 rAF 会暂停，滚动窗口会空白）
    if (winTimerRef.current) return;
    winTimerRef.current = window.setTimeout(() => {
      winTimerRef.current = 0;
      const el2 = msgsRef.current;
      if (el2) setWin(computeWindow(el2.scrollTop, el2.clientHeight, heightStoreRef.current!, rowsRef.current));
    }, 16);
  };


  /*
   * [v0.9d Issue 2] ★ 三处人数必须是同一个数。
   *
   *   之前：左栏（ConvList）和花名册（RosterPanel）都滤掉了已归档的人，
   *   **只有这儿的标题没滤** —— 于是同一个群，标题说「3 位成员」、
   *   花名册说「成员 · 2」、左栏说「2 人」。三个数字，三种说法，
   *   用户不知道该信谁（他会信最大的那个，然后发现少了一个人）。
   *
   *   归档的人不算「成员」：他不再接活了。他还在 members 数组里，
   *   是因为历史气泡要靠它认脸——那是另一回事。
   */
  const live = useMemo(
    () => members.filter((m) => m.status !== 'removed'),
    [members],
  );

  const busyMembers = useMemo(
    () => live.filter((m) => m.state === 'busy'),
    [live],
  );
  const busyCount = busyMembers.length;
  const standbyMembers = useMemo(() => live.filter((m) => m.state === 'standby'), [live]);
  const standbyCount = standbyMembers.length;
  const busyLabel = useMemo(() => {
    const roles = Array.from(new Set(
      busyMembers.map((m) => roleLabel(m.display.role) || m.display.name).filter(Boolean),
    ));
    const shown = roles.slice(0, 3).join('/');
    if (roles.length > 3) return t('chat.stream.workingMulti', { shown, n: roles.length });
    if (roles.length === 1) return t('chat.stream.workingSingle', { name: roles[0] });
    return t('chat.stream.working', { shown });
  }, [busyMembers]);

  /*
   * [v0.8b #10] 私聊（知知）：没有人数、没有头像堆、没有花名册面板。
   *
   *   她是一个人，不是一个群。「1 位成员」这行字出现在跟她的对话框上，
   *   就跟微信在跟妈妈的聊天顶上写「本群 1 人」一样。
   *
   *   顺带把花名册面板关掉：如果用户是在群里开着面板切过来的，
   *   面板会挂在那儿显示一个空名单 —— 而且聊天区的宽度还会跟着变（#4）。
   */
  const priv = isPrivateChat(projectId);

  // [v1.0.23.5] ★ 删除原「priv && rosterOpen → onToggleRoster」effect：
  //   多实例常驻后，私有会话实例（知知/DM）共享 rosterOpen 状态——在群聊点头像堆时
  //   rosterOpen 变 true，常驻的知知实例该 effect 立即触发 onToggleRoster() 把面板关掉
  //   （「花名册闪开又关、出不来」的根因）。
  //   语义（切到私聊时关面板）已上移到 App 层 useLayoutEffect（activeId 变化时处理）。

  // [v0.37] 群内私聊窗口里，气泡的**脸**要和群里一致：头像种子用「所属群」而不是私聊频道 id
  //   （否则同一个人在私聊里会算出另一张脸）；项目经理命名也用群名，避免「群名 · 项目经理 · 项目经理」。
  const dmGroup = isAgentDm(projectId) ? dmGroupOf(projectId) : null;
  const faceSeedPid = dmGroup || projectId || '';
  const groupName = useKnoweStore((s) => (dmGroup ? s.convs[dmGroup]?.projectName || dmGroup : ''));
  const faceName = dmGroup ? groupName : name;

  // [v1.0.23.13] ★ face 对象缓存：faceOf() 每次调用返回**新对象**，直接传进 memo 组件
  //   会击穿 MessageBubble 的 React.memo（浅比较引用）→ ChatStream 任何一次重渲染
  //   （成员状态变化/多选/翻译等）都拖垮整条消息流。这里按 agentId 缓存 face 对象，
  //   members/种子/名字不变时同一 agent 永远拿到同一引用。成员真实变化时 members
  //   引用变 → 整表重建（成本一次，换来的是常态渲染零击穿）。
  const faceCache = useMemo(() => {
    const cache = new Map<string, AgentFace>();
    return (agentId: string): AgentFace => {
      let f = cache.get(agentId);
      if (!f) {
        f = faceOf(members, agentId, faceSeedPid, faceName);
        cache.set(agentId, f);
      }
      return f;
    };
  }, [members, faceSeedPid, faceName]);

  // [v0.38.6 #4] 群内 Agent 私聊：标题下那行显示**这个成员的职责（角色）**，
  //   而不是千篇一律的「私聊 · 随时可以说话」。角色取自花名册成员的 display.role
  //   （形如「前端」「后端」「UI/UX 设计」）。先看当前会话成员，再回退到所属群花名册，
  //   两处都查不到才落回原来的通用话术。
  const dmAgentId = isAgentDm(projectId) ? (parseDmId(projectId)?.agentId ?? null) : null;
  const dmGroupMembers = useKnoweStore(
    (s): Member[] => (dmGroup ? (s.convs[dmGroup]?.members ?? []) : []),
  );
  const dmRole = useMemo(() => {
    if (!dmAgentId) return '';
    const m = members.find((x) => x.id === dmAgentId)
      || dmGroupMembers.find((x) => x.id === dmAgentId);
    return roleLabel(m?.display.role || '').trim();
  }, [dmAgentId, members, dmGroupMembers]);

  // [v0.37] 群聊里双击某个 agent 的发送者行/头像 → 进入与他的私聊。
  //   私聊/知知窗口里不给这个能力（priv=true → onOpenDm 传 undefined，气泡不可双击）。
  const enterDm = useKnoweStore((s) => s.enterDm);
  const onOpenDm = (!priv && projectId)
    ? (agentId: string) => {
      const member = members.find((row) => row.id === agentId);
      if (member?.state === 'busy' || member?.state === 'standby') {
        toast(member.state === 'standby'
          ? t('chat.stream.relayStandby', { name: memberNameLabel(member.id, member.display.name) })
          : t('chat.stream.relayWorking', { name: memberNameLabel(member.id, member.display.name) }));
        return;
      }
      enterDm(projectId, agentId);
    }
    : undefined;
  const onAgentContextMenu = projectId
    ? (e: React.MouseEvent, agentId: string): void => {
      openAgentMenu(projectId, agentId, e.clientX, e.clientY);
    }
    : undefined;

  const stack = priv ? [] : live.slice(0, 4);          // [v0.9d] 头像堆也不放归档的人

  const toggleRosterFromHeader = (): void => {
    useTokenUsageStore.getState().closePanel();
    useRecordsStore.getState().closeDrawer();
    onToggleRoster();
  };

  const openRecordsFromHeader = (): void => {
    useTokenUsageStore.getState().closePanel();
    if (rosterOpen) onToggleRoster();
    useRecordsStore.getState().openDrawer();
  };

  const openProjectMoreMenu = (event: React.MouseEvent<HTMLButtonElement>): void => {
    // [v1.0.20.1-M3] 旧「更多」菜单（仅含 Token 消耗一项）升级为独立 Token 箭头按钮：
    // 点击直接开合统计抽屉，不再走弹出菜单。保留本函数签名供外部引用兼容。
    void event;
    toggleTokenPanel();
  };

  const toggleTokenPanel = (): void => {
    // [v1.0.20.1-M3+] 群聊 / 知知平台会话 / 群内私聊（worker 窗口）都有 Token 统计；
    // 私聊窗口看所属群的整体消耗（与 RecordsDrawer 同一语义）。
    const tokenPanelVisible = recordsIsGroup || projectId === PLATFORM_PROJECT_ID || isAgentDm(projectId);
    if (!tokenPanelVisible || !projectId) return;
    const state = useTokenUsageStore.getState();
    if (state.open) {
      state.closePanel();
      return;
    }
    // 打开时与其他抽屉互斥（Roster / Records），并默认近 7 天。
    if (rosterOpen) onToggleRoster();
    useRecordsStore.getState().closeDrawer();
    // 私聊 DM → 解析出所属群 id（后端聚合响应 canonical 群 id，store 匹配需要一致）。
    const tokenProjectId = isAgentDm(projectId) ? (dmGroupOf(projectId) ?? projectId) : projectId;
    useTokenUsageStore.getState().openPanel(tokenProjectId);
  };

  return (
    <SessionActiveContext.Provider value={active}>
      {/* ═══ 头部 ═══ */}
      <header className={'chat-head' + (scrolled ? ' scrolled' : '')}>
        <div className="ch-info">
          <div className="ch-title">{name}</div>
          <div className="ch-status">
            {priv ? (
              /* 私聊：说她在不在干活，不说「几个人」 */
              busyCount > 0 ? (
                <>
                  <span className="live-dot" />
                  <span>{t('chat.stream.21')}</span>
                </>
              ) : (
                /*
                 * [v0.8c #2a] 知知不是「某个私聊对象」，她是**平台接待**——
                 * 你还没有项目的时候，是她把你脑子里那团东西理成一件能开工的事。
                 * 将来群内 Agent 的私聊窗口走下面那句通用的。
                 */
                <span>
                  {projectId === PLATFORM_PROJECT_ID
                    ? t('chat.stream.17')
                    : (dmRole || t('chat.stream.22'))}
                </span>
              )
            ) : busyCount > 0 ? (
              <>
                <span className="live-dot" />
                <span>{busyLabel}{standbyCount ? t('chat.stream.standbySuffix', { n: standbyCount }) : ''}</span>
              </>
            ) : standbyCount > 0 ? (
              t('chat.stream.standbySuffix', { n: standbyCount })
            ) : live.length > 0 ? (
              t('chat.stream.memberCount', { n: live.length })
            ) : (
              t('chat.stream.16')
            )}
          </div>
        </div>
        <div className="ch-actions">
          {/* [v1.0.23.5] Token 消耗统计入口：从 msgs 顶部悬浮移到头部按钮组（添加员工左侧），
              动效照抄 InteractiveHoverButton（圆点膨胀 + 文字右滑 + 箭头，箭头改下箭头）。 */}
          {(recordsIsGroup || projectId === PLATFORM_PROJECT_ID || isAgentDm(projectId)) && (
            <button
              className={'tk-msgs-toggle effect-btn' + (tokenPanelOpen ? ' active' : '')}
              data-tip={t('chat.stream.01')}
              aria-label={t('chat.stream.01')}
              aria-expanded={tokenPanelOpen}
              data-token-usage-toggle
              onClick={openProjectMoreMenu}
            >
              <span className="eb-text">{t('chat.stream.01')}</span>
              <span className="eb-cover">
                <span>{t('chat.stream.01')}</span>
                <IconDown />
              </span>
            </button>
          )}
          {/* [v1.0.23.4] 群聊中途添加 Agent 员工：仅群聊显示（私聊/知知隐藏） */}
          {!priv && projectId !== PLATFORM_PROJECT_ID && (
            <button
              className="btn-add-agents effect-btn"
              data-tip={t('chat.head.addAgents')}
              aria-label={t('chat.head.addAgents')}
              onClick={(e) => setAddAgentsAnchor(
                addAgentsAnchor ? null : { x: e.clientX, y: e.clientY },
              )}
            >
              {/* [v1.0.23.5] 照抄 InteractiveHoverButton：去加号，未激活只显示文字；
                  hover → 圆点膨胀铺满 + 文字右滑 + 白字箭头覆盖层滑入 */}
              <span className="eb-text">{t('chat.head.addAgents')}</span>
              <span className="eb-cover">
                <span>{t('chat.head.addAgents')}</span>
                <IconChevR />
              </span>
            </button>
          )}
          {stack.length > 0 && (
            <div
              className="stack"
              title={t('chat.stream.20')}
              role="button"
              tabIndex={0}
              aria-label={t('chat.stream.20')}
              aria-expanded={rosterOpen}
              data-roster-toggle          /* [v0.10b Bug5] 点外部收起时忽略这个触发按钮 */
              onClick={toggleRosterFromHeader}
              onKeyDown={(e) => { if (e.key === 'Enter') toggleRosterFromHeader(); }}
            >
              {stack.map((m, index) => {
                const working = m.state === 'busy';
                const standby = m.state === 'standby';
                return (
                  <span
                    key={m.id}
                    className={working ? 'stack-avatar working' : standby ? 'stack-avatar standby' : 'stack-avatar'}
                    title={working ? t('chat.stream.workingWithRole', { name: m.display.name, role: roleLabel(m.display.role) }) : standby ? t('chat.stream.standbyWithRole', { name: m.display.name, role: roleLabel(m.display.role) }) : m.display.name}
                    style={{
                      display: 'inline-flex', borderRadius: '50%',
                      marginLeft: index === 0 ? 0 : -7,
                      position: 'relative', zIndex: stack.length - index,
                      boxShadow: working
                        ? '0 0 0 2px var(--accent, #4f7cff), 0 0 12px rgba(79,124,255,.52)'
                        : standby ? '0 0 0 2px rgba(79,124,255,.35)' : undefined,
                      opacity: standby ? .72 : 1,
                    }}
                  >
                    {/* [v0.5b #5] 头像堆补齐真实图片；v0.15 忙碌头像按最近开工时间居左。 */}
                    <Avatar
                      glyph={m.display.glyph}
                      pal={m.display.pal}
                      size={28}
                      src={m.display.avatarUrl ?? faceFor(m.id, projectId || '', name).avatarUrl}
                    />
                  </span>
                );
              })}
            </div>
          )}
          <button
            className="icon-btn"
            data-tip={t('chat.stream.05')}
            aria-label={t('chat.stream.04')}
            data-roster-toggle
            onClick={openRecordsFromHeader}
          >
            <IconSearchSm />
          </button>
        </div>
      </header>

      {/* [v1.0.23.4] 群聊中途添加 Agent 员工 Popover（卡片左上角跟随鼠标） */}
      {addAgentsAnchor && projectId && (
        <AddAgentsPopover
          projectId={projectId}
          anchor={addAgentsAnchor}
          onClose={() => setAddAgentsAnchor(null)}
        />
      )}

      {/* ═══ 消息流 ═══ */}
      <div
        className="msgs-wrap"
        aria-hidden={recordsOpen || undefined}
        onDragEnter={onZoneDragEnter}
        onDragOver={onZoneDragOver}
        onDragLeave={onZoneDragLeave}
        onDrop={onZoneDrop}
      >
      {/* [v1.0.19.4] 拖拽雾化遮罩：半透明 + 背景模糊 + 中央提示卡（带轻微弹感）。 */}
        {dragging && (
          <div className="dropzone" role="presentation">
            <div className="dz-card">
              <span className="dz-icon" aria-hidden="true"><IconClip /></span>
              <span className="dz-text">{t('chat.stream.19')}</span>
            </div>
          </div>
        )}
        {banner && !bannerHidden && (
          <div className="banner" role="status">
            <IconRecover />
            <span>{banner}</span>
            <button className="bx" aria-label={t('chat.stream.09')} onClick={() => setBannerHidden(true)}>
              <IconX />
            </button>
          </div>
        )}

        {/* ═══ [v0.40.0/.1] 多选操作条（README §3.4；DOM 照 component-tree §E · Selbar） ═══
            转发 → 逐条构造转发内容真正投递；收藏 → 合并为一张卡（#3）；复制 → 汇总正文到剪贴板；
            删除 → 批量视图删除。 */}
        {selecting && (
          <Selbar
            count={Object.keys(selectedKeys).length}
            onForward={() => {
              const picked = items.filter((it, i) => selectedKeys[itemKeyOf(it, i)]);
              // [v1.0.23.1] 来源群名：私聊会话取父群名（与 favorites.projectLabelOf 同口径）。
              const srcConv = useKnoweStore.getState().convs[projectId || ''];
              const payload = picked
                .filter((it) => it.kind === 'user' || it.kind === 'agent')
                .map((it, idx): ForwardItem => {
                  const realIdx = items.indexOf(it);
                  const f = it.kind === 'agent' ? (it.files ?? []) : [];
                  const who = it.kind === 'user'
                    ? '我'
                    : faceCache(it.agentId).name;
                  return {
                    text: it.text,
                    files: f.length ? f : undefined,
                    markdown: it.kind === 'agent',
                    sourceName: who,
                    sourceProjectName: srcConv?.parentProjectName || srcConv?.projectName,
                    sourceRef: { projectId: projectId || '', itemKey: itemKeyOf(it, realIdx >= 0 ? realIdx : idx) },
                  };
                });
              if (payload.length) openForwardPicker(payload);
            }}
            onFavorite={() => {
              const picked = items.filter((it, i) => selectedKeys[itemKeyOf(it, i)]);
              const n = picked.length;
              const conv = useKnoweStore.getState().convs[projectId || '']
                ?? { projectId: projectId || '', projectName: name, items: [], members: [], banner: null, draft: '', unread: 0, };
              // #3 合并为一张卡（摘要含各条概览）。
              useFavoritesStore.getState().addMerged({
                conv,
                items: picked,
                isGroup: !priv && projectId !== PLATFORM_PROJECT_ID,
                pal: 'av-n',
              });
              toast(t('chat.stream.favoritedN', { n }));
              useKnoweStore.getState().exitSelect();
            }}
            onCopy={() => {
              const picked = items.filter((it, i) => selectedKeys[itemKeyOf(it, i)]);
              const text = picked
                .map((it) => (it.kind === 'user' || it.kind === 'agent' ? it.text : ''))
                .filter(Boolean)
                .join('\n\n');
              void navigator.clipboard?.writeText(text);
              toast(t('chat.stream.copiedN', { n: picked.length }));
              useKnoweStore.getState().exitSelect();
            }}
            onDelete={() => {
              const keys = Object.keys(selectedKeys);
              confirmModal({
                title: t('chat.stream.deleteConfirmTitle', { n: keys.length }),
                body: t('chat.stream.07'),
                okLabel: t('chat.stream.10'),
                danger: true,
                onOk: () => {
                  useKnoweStore.getState().removeItemsFromView(projectId || '', keys);
                  toast(t('chat.stream.deletedN', { n: keys.length }));
                  useKnoweStore.getState().exitSelect();
                },
              });
            }}
            onCancel={() => useKnoweStore.getState().exitSelect()}
          />
        )}

        <div className={'msgs' + (selecting ? ' selecting' : '')} ref={msgsRef} onScroll={onScroll} onWheel={onWheel}>
          <div className="vlist" style={{ position: 'relative', height: totalH }}>
            {(() => {
              const w = win.end >= win.start ? win : { start: 0, end: Math.min(rows.length - 1, 30) };
              const out: React.ReactNode[] = [];
              for (let j = w.start; j <= w.end && j < rows.length; j++) {
                const row = rows[j];
                if (!row) continue;
                const it = row.itemIndex >= 0 ? deferredItems[row.itemIndex] : undefined;
                // [v0.10b Bug2] 分组/分隔线用「预计算的可见邻居」——窗口外的邻居也能正确判定。
                const prevRow = row.prevVisible >= 0 ? rows[row.prevVisible] : undefined;
                const prevIt = prevRow && prevRow.itemIndex >= 0 ? deferredItems[prevRow.itemIndex] : undefined;
                const nextRow = row.nextVisible >= 0 ? rows[row.nextVisible] : undefined;
                const nextIt = nextRow && nextRow.itemIndex >= 0 ? deferredItems[nextRow.itemIndex] : undefined;
                const key = senderKey(it);
                const grouped = key !== null && key === senderKey(prevIt);
                const tail = key === null || key !== senderKey(nextIt);

                // [v1.0.24.4] isNew 提前到 node 之前计算（relay 检测/approval 分支要用）。
                //   不再在渲染循环里立即 seenIks.add：新行的入场动画（≤0.8s）期间任何
                //   重渲染（ResizeObserver 高度校正 / useDeferredValue 二次渲染）都会把
                //   isNew 翻成 false → vrow 加回 no-anim → animation:none 掐断正在播的
                //   展开动画 → 卡片闪现。入册交给上方 settle timer：动画播完（1s）才标记历史。
                const seenIks = mountSeenRef.current ?? (mountSeenRef.current = new Set(rows.map((r) => r.ik)));
                const isNew = !seenIks.has(row.ik);

                // [v1.0.24.4-r10] 派卡接力对检测：本行是刚定格的推理气泡（agent + 非 streaming
                //   + 无 text + 有 reasoning），且**下一行**是**新**审批卡 → 启动接力动画：
                //   气泡行（原气泡）收起 + 卡片行（原卡片）展开，两行独立渲染，
                //   由 relaySync effect 的 rAF 逐帧驱动两行高度并同步 HeightStore（零错位）。
                //   [v1.0.24.4-r14] 重写：不再合并渲染 RelayMorph（重渲染气泡会丢状态/叠影），
                //   气泡行保留原 MessageBubble DOM，卡片行独立 approval-slot。
                const relayInfo0 = relayInfoRef.current;
                // [v1.0.24.6-P0] 隐藏会话停摆：不建立接力对（省 relay 动画 rAF + 避免恢复时误触发）
                if (!relayInfo0 && active && it?.kind === 'agent' && !it.streaming
                  && !(it.text && it.text.trim()) && !!it.reasoning
                  && nextRow && nextIt?.kind === 'approval' && !seenIks.has(nextRow.ik)) {
                  relayInfoRef.current = {
                    bubbleIk: row.ik,
                    bubbleRowIdx: j,
                    cardIk: nextRow.ik,
                    cardRowIdx: row.nextVisible,
                    done: false,
                  };
                  // [v1.0.24.4-r12] 保护两行：rebuild 的幽灵行删除会与 rAF measure 竞态
                  //   （prev=undefined → 估算 delta 污染 → 终态重叠），动画期间禁止删除。
                  heightStore.protect(row.ik);
                  heightStore.protect(nextRow.ik);
                }
                // 检测后重新读取（本帧刚建立接力对时 isRelayBubble 也要为 true）
                const relayInfo = relayInfoRef.current;
                const isRelayBubble = !!relayInfo && relayInfo.bubbleIk === row.ik;
                const isRelayCard = !!relayInfo && relayInfo.cardIk === row.ik;
                if (!shouldRenderRow(row) && !isRelayBubble) continue;   // 幽灵空气泡：不占位、不画分隔线
                // [v1.0.24.4-r14] 接力卡片行：正常渲染（approval-slot），高度由 relaySync 的
                //   rAF 驱动（0→卡高）→ 卡片从气泡位置向下生长。不做任何特殊处理。

                // [v0.38] 时间分隔线：只在两条「消息」之间、间隔 ≥ 4 分钟处插入。
                const curMs = msOf(it);
                const showDivider = row.isMsg && !row.ghost
                  && shouldShowDivider(msOf(prevIt), curMs);
                const divider = showDivider && curMs != null
                  ? <div className="time-divider"><span>{formatDividerLabel(curMs)}</span></div>
                  : null;

                const node = ((): React.ReactNode => {
                  if (!it) return null;
                  if (it.kind === 'system') {
                    return <SystemLine text={it.text} level={it.level} />;
                  }

                  if (it.kind === 'approval') {
                    // [v1.0.24.4-r10] 卡片行：接力对中由 RelayMorph 接管（气泡行位置渲染），
                    //   这里不渲染（isRelayCard 已在上面 continue）。普通/历史卡片走原路径。
                    const cardNode = (
                      /* [v1.0.24.4] approval-slot：审批卡幕布式 morph 入场的外层载体
                         （grid 0fr→1fr 推动下方消息流腾位，动画定义在 knowe-components.css） */
                      <div className="approval-slot">
                        <ApprovalCard
                          cardId={it.cardId}
                          projectId={it.projectId || projectId || ''}
                          tool={it.tool}
                          card={it.card}
                          state={it.state}
                          expiresAt={it.expiresAt}
                          members={members}
                          rev={it.rev}
                        />
                      </div>
                    );
                    return cardNode;
                  }

                  if (it.kind === 'agent') {
                    // [v1.0.24.4-r14] 接力气泡行：**保留原 MessageBubble DOM**（不重渲染、
                    //   不丢状态），推理面板 forceReasoningOpen 保持展开（收起动画从完整高度
                    //   开始）。vrow 高度由 relaySync 的 rAF 驱动塌缩（H→0）+ overflow hidden。
                    //   气泡行是幽灵行（无 text 非 streaming）——放行条件 isRelayBubble 在上面。
                    if (isRelayBubble && relayInfo && !relayInfo.done) {
                      retractAnimRef.current = true;
                    }
                    // [v1.0.23.4] 统一壳：streaming 期间也用 MessageBubble（bubble agent tail），
                    //   三点 → 推理(live) → 落定正文 morph，全程同一 DOM 节点，不跳闪。
                    // 空气泡守卫（双保险：applyEvent 已挡一层；streaming 三点阶段放行）
                    // [v0.36] 有 files 的空文本气泡放行：它要渲染文件卡片。
                    // [v1.0.24.4-r14] 接力中的气泡行放行（它在播收起动画，不是历史幽灵行）。
                    if (!isRelayBubble && !it.streaming && !it.text && !(it.files && it.files.length)) {
                      // 历史行（刷新/滚动重挂载）：定格气泡已收起过 → 不占位（0 高幽灵行）。
                      return null;
                    }
                    return (
                      <MessageBubble
                        kind="agent"
                        text={it.text}
                        grouped={grouped}
                        tail={tail}
                        face={faceCache(it.agentId)}
                        agentId={it.agentId}
                        onOpenDm={selecting ? undefined : onOpenDm}   /* [v0.40.0] 多选时点击=选中，不进私聊 */
                        onAgentContextMenu={onAgentContextMenu}
                        files={it.files}
                        projectId={projectId || ''}
                        domSeq={it.seq}
                        itemKey={row.ik}
                        onContextMenu={(e) => msgMenu(e, it, row.itemIndex)}
                        selecting={selecting}
                        selected={!!selectedKeys[row.ik]}
                        onToggleSelect={() => useKnoweStore.getState().toggleSelect(row.ik)}
                        /* [v1.0.23.3] 三模块：推理 + 四方向按钮 */
                        reasoning={it.reasoning}
                        reasoningSeconds={it.reasoningSeconds}
                        suggestions={it.suggestions}
                        onSuggestionSend={suggestionSendFor(it.agentId)}
                        /* [v1.0.24.4-r13/r14] 接力气泡行/待派卡气泡：推理面板保持展开做收起动画 */
                        forceReasoningOpen={isRelayBubble || !!((it as { relayPending?: boolean }).relayPending)}
                        /* [v1.0.23.4] 统一壳 streaming 扩展（原 StreamBubble 职责并入） */
                        streaming={it.streaming}
                        frameId={it.transientFrame?.id}
                        onFramePaint={onTransientFramePaint}
                        settling={it.transientFrame?.settlePending}
                      />
                    );
                  }

                  return (
                    <MessageBubble
                      kind="user"
                      text={it.text}
                      grouped={grouped}
                      tail={tail}
                      delivery={it.delivery}
                      domSeq={it.seq}
                      itemKey={row.ik}
                      onContextMenu={(e) => msgMenu(e, it, row.itemIndex)}
                      selecting={selecting}
                      selected={!!selectedKeys[row.ik]}
                      onToggleSelect={() => useKnoweStore.getState().toggleSelect(row.ik)}
                      quote={it.quote}
                      onJumpQuote={jumpToItemKey}
                      files={it.files}
                      projectId={projectId || ''}
                      forwarded={it.forwarded}
                      attachments={it.attachments}
                    />
                  );
                })();

                if (node == null) continue;
                // [v1.0.24.4] 新审批卡在播：贴底 effect 据此平滑滚动/跟随。
                //   换新卡时重置用户滚轮抑制（上一张卡用户介入过，不影响这张）。
                if (isNew && it?.kind === 'approval') {
                  if (newCardIkRef.current !== row.ik) {
                    newCardIkRef.current = row.ik;
                    suppressScrollRef.current = false;
                  }
                  newCardAnimRef.current = true;
                }
                out.push(
                  <div
                    key={row.reactKey}
                    className={'vrow' + (isNew ? '' : ' no-anim') + (isRelayCard ? ' no-anim' : '')}
                    data-ik={row.ik}
                    data-index={j}
                    style={{
                      position: 'absolute',
                      // [v1.0.24.4-r14] relay 动画中的两行：top 由 relaySync 的 rAF 每帧独占写
                      //   （React 渲染滞后一帧 → 高度变了位置没变 → 瞬态重叠）。rAF 同帧
                      //   「算高→measure→写 top」→ 绘制时必然一致（零错位）。
                      // [v1.0.25.3-卡片置顶修复] relay 让位条件加**实际状态**校验：
                      //   relayInfo/relayStartedRef 只是「意图」，rAF 可能因切群交错/隐藏
                      //   会话停摆从未写出内联 top（实测：官网设计群卡片行 style.top 永远空
                      //   → 绝对定位退化为 static → 排到消息流最上方第 1 行）。
                      //   只有该行 DOM 的 style.top **真实存在**（rAF 写过）才让位；
                      //   否则渲染循环兜底写 top——位置永远正确，动画只是增强。
                      //   同时主动自愈：清理残留 relay 状态（后续新卡片才能再建接力对）。
                      // [v1.0.24.7-P0-1-防线1] 渲染循环 fallback：relay 行挂着但 rAF **未在跑**
                      //   （relayStartedRef=false，如启动失败/active 翻转后未恢复）→ 仍写 top，
                      //   位置永远正确——动画只是增强，不是位置正确性的前提。rAF 启动后
                      //   relayStartedRef=true，渲染循环继续让位（避免与 rAF 竞争写 top）。
                      ...(relayInfo && (relayInfo.bubbleIk === row.ik || relayInfo.cardIk === row.ik)
                        && relayStartedRef.current
                        ? (() => {
                            const relayEl = rowElsRef.current.get(row.ik);
                            if (relayEl && relayEl.style.top) return {};
                            // rAF 未写 top（残留/未启动）→ 兜底写 + 自愈清理
                            relayInfoRef.current = null;
                            relayStartedRef.current = false;
                            return { top: heightStore.topOf(j) };
                          })()
                        : { top: heightStore.topOf(j) }),
                      left: 0,
                      right: 0,
                    }}
                    ref={(el) => {
                      if (el) rowElsRef.current.set(row.ik, el);
                      else rowElsRef.current.delete(row.ik);
                    }}
                  >
                    {divider}
                    {node}
                  </div>,
                );
              }
              return out;
            })()}
          </div>

          {/*
            [v0.13 卡片位置修正] 目录恢复卡片就在消息流里、群聊头部下方，和审批卡同一处。
            它不属于 items（来自独立的目录 store），所以挂在消息列表**末尾**——
            目录失效是最新发生的事，卡片理应出现在对话当前的最下方，正如一张刚弹出的审批卡。
            没有待处理目录时组件自渲染为 null，不占位。
          */}
          <DirectoryRecoveryCard projectId={projectId} />
        </div>

        <button
          className={'fab-bottom' + (atBottom ? '' : ' show')}
          aria-label={t('chat.stream.12')}
          onClick={() => {
            const el = msgsRef.current;
            // [v1.0.24.4] fab 接入自定义平滑滚动：0.5s 慢→快→慢（cubic-bezier(.77,0,.175,1)），
            //   目标每帧重算贴住实时底部；原生 scrollTo smooth 是平台曲线且时长不可控。
            if (el) smoothScrollBottom(el);
          }}
        >
          <IconDown />
        </button>
      </div>

      {/* [v1.0.23.5] RecordsDrawer / TokenUsagePanel 为全局覆盖层，已上提到 App 层
          （.chat-card 直接子级）——会话常驻后不随实例重复挂载。 */}
    </SessionActiveContext.Provider>
  );
};

/**
 * [v0.40.0] 多选操作条（component-tree §E · Selbar）：
 *   .selbar > span.n「已选 N 条」 + span.sp + selbar-btn ×4 + 取消
 * 文案/顺序/danger 位照抄 reference updateSelbar（2626 行）；「取消」带 6px 左距（同 reference 内联）。
 */
const Selbar: React.FC<{
  count: number;
  onForward: () => void;
  onFavorite: () => void;
  onCopy: () => void;
  onDelete: () => void;
  onCancel: () => void;
}> = ({ count, onForward, onFavorite, onCopy, onDelete, onCancel }) => {
  const { t } = useCachedT();
  return (
    <div className="selbar" role="toolbar" aria-label={t('chat.stream.15')}>
      <span className="n">{t('chat.stream.selectedN', { n: count })}</span>
      <span className="sp" />
      <button className="selbar-btn" onClick={onForward}><IconForward />{t('chat.stream.06')}</button>
      <button className="selbar-btn" onClick={onFavorite}><IconStar />{t('common.09')}</button>
      <button className="selbar-btn" onClick={onCopy}><IconCopy />{t('chat.stream.13')}</button>
      <button className="selbar-btn danger" onClick={onDelete}><IconTrash />{t('chat.stream.10')}</button>
      <button className="selbar-btn" style={{ marginLeft: 6 }} onClick={onCancel}>{t('chat.stream.03')}</button>
    </div>
  );
};

export default ChatStream;
