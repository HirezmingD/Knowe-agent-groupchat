/**
 * [v1.0.13][R1] Backend settings are reconciled before first-run entry; local state is not blind-pushed.
 * App.tsx — 桌面窗口外壳 + 应用骨架 + socket 生命周期
 *
 * DOM（component-tree §A · DeskWindow + AppShell）：
 *   .desk > .win > (.titlebar > .title-mid + .traffic)          ← [v1.0 frameless] 自定义标题栏
 *                + (.app > nav.rail + (.views > .view.view-chats + .view.view-alt))
 *   （v0.5 之前的 .traffic + .title-mid + .title-right 三段式不再复刻：
 *     现在左段 .title-mid 只是可拖拽留白，右段 .traffic 是真·窗口控制点。）
 *
 * ★ 唯一允许 import transport 的组件（socket 的家在这里）。
 *   其余组件一律只 import selectors + store actions。
 *
 * 回调接线（socket → store），逐条对应 SocketCallbacks：
 *   onEvent            → handleEvent      （事件入状态机）
 *   onStatus           → setConnStatus    （六态 → ConnBadge）
 *   onEchoOk           → confirmEcho      （乐观气泡 pending → confirmed）
 *   onEchoLost         → suspectEcho      （5s 无回声 → suspect，屏幕上必须看得见）
 *   onEpochReset       → clearProject     （server 重启 → 清会话等重同步）
 *   onProjectDiscovered→ ensureProject    （回放里发现的项目自动上列表）
 *   onProjectDirectory*→ 原生目录选择器   （系统控制流，不进聊天时间线）
 *   getActiveProjectId → store 快照       （replay_request 的项目上下文）
 *
 * ⚠ 不接 onSent：store.sendMessage 自己已经插了 pending 气泡，
 *   再接 onSent 会插第二个（双气泡）。这里刻意留空。
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import { createSocket, initWsAuthToken } from '../transport/socket';
import { warmUpIncremental } from '../store/incrementalSync';   // [v1.0.23.6] 启动预热
import { record as recordDiagnostic } from '../observe/corridor';
import { useKnoweStore } from '../store/store';
import { selectActiveView, selectActiveProjectId } from '../store/selectors';

/*
 * [v1.0.23.2] socket 全应用单实例（模块级标记）。
 *
 * 背景：原实现用 useRef(bootedRef) 防重复挂载，但 useEffect cleanup 里
 * `bootedRef.current = false` + `socket.disconnect()` —— App 每次 remount
 * （Fast Refresh 组件更新）都会销毁旧 socket 重建新实例。多次更新后旧 WS
 * 连接泄漏、多实例并存，每个连接都收到后端广播 → 同一条消息被 applyEvent
 * 多次（实测 5 个连接 → 5 条相同回复气泡）。
 *
 * 模块级变量不随组件 remount 重置：socket 只建一次，cleanup 不再销毁，
 * HMR/重挂载时连接保持（也避免了重连全量重放）。
 */
let _appSocketBooted = false;

/**
 * [v1.0.24.6-P2] 事件批量消费：16ms rAF 窗口合并渲染。
 *
 * 现状：每条 WS 帧（stream_delta/reasoning_delta 每秒几十条）是独立 onmessage 回调
 * → 独立 handleEvent → 独立 setState → 独立渲染。React 18 自动批处理只合并
 * 「同一同步栈」内的多次 setState，跨回调不合并 → 每秒几十次渲染。
 *
 * 机制：handleEvent 入队列，rAF 驱动的 16ms 窗口结束统一消费——同一 flush 回调内
 * 逐条 handleEvent（React 18 自动批处理覆盖 rAF 回调）→ 每帧最多一次渲染。
 *
 * 语义保持：
 * - 队列 FIFO 逐条执行，isBumpEvent/水位/去重顺序与现状完全一致（只延迟不丢弃）；
 * - 控制帧（replay_complete/state_snapshot/resync_required）在 socket.ts 内部
 *   routeEvent 旁路，不经过 onEvent，天然不被缓冲；
 * - Token 查询响应在 enqueue 之前已由 handleTokenUsageEvent 旁路消费。
 *
 * 兜底：窗口隐藏/后台时 rAF 被节流或暂停 → 50ms setTimeout 强制 flush，防队列堆积。
 * 模块级变量不随组件 remount 重置（与 _appSocketBooted 同理）。
 */
let _eventQueue: unknown[] = [];
let _flushScheduled = false;

function flushEvents(): void {
  _flushScheduled = false;
  if (_eventQueue.length === 0) return;
  const batch = _eventQueue;
  _eventQueue = [];
  const st = useKnoweStore.getState();
  for (const ev of batch) {
    st.handleEvent(ev as Parameters<typeof st.handleEvent>[0]);
  }
}

function enqueueEvent(ev: unknown): void {
  _eventQueue.push(ev);
  if (!_flushScheduled) {
    _flushScheduled = true;
    requestAnimationFrame(flushEvents);
    // 兜底：rAF 被节流/暂停（窗口隐藏）时强制 flush，事件延迟 ≤50ms。
    setTimeout(() => { if (_flushScheduled) flushEvents(); }, 50);
  }
}
import {
  bindSocket as bindDirectorySocket,
  openRequest as openDirectoryRequest,
  resolve as resolveDirectory,
  syncFromProjectCreated as syncDirectoryFromProjectCreated,
} from '../store/directoryRecovery';

import Rail from '../components/Rail';
import ConvList from '../components/ConvList';
import ConnBadge from '../components/ConnBadge';
import { PLATFORM_PROJECT_ID, getZinniaDisplayName } from '../store/avatar';
import { isPrivateChat } from '../store/chat';
import type { UnreadDetail } from '../shared/bridge';
import BackendGate from '../components/BackendGate';
import SessionHost from '../components/SessionHost';
import Composer from '../components/Composer';
import RecordsDrawer from '../components/RecordsDrawer';
import TokenUsagePanel from '../components/TokenUsagePanel';
import EmptyState from '../components/EmptyState';
import RosterPanel from '../components/RosterPanel';
import ToastHost from '../components/ToastHost';
import DevDrawer from '../components/DevDrawer';
import ContactsView from '../components/ContactsView';
import FavoritesView from '../components/FavoritesView';
import KnowledgeView from '../components/KnowledgeView';
import SettingsView from '../components/SettingsView';
import CommandPalette, { type GlobalSearchTarget } from '../components/GlobalSearch';
import { useKnowledgeStore } from '../store/knowledge';
import FloatingLayers from '../components/ContextMenu';
import FirstRunModelGate from '../components/FirstRunModelGate';
import { useSettingsStore } from '../store/settings';
import { useRecordsStore } from '../store/records';
import { bindTokenUsageSocket, handleTokenUsageEvent, useTokenUsageStore, } from '../store/tokenUsage';

import { invokeWindowControl } from '../shared/windowControl';

/**
 * [v1.0.20.2] 未读明细的时间标签：今天 → HH:mm；昨天 → 「昨天」；
 * 今年 → M月D日；更早 → YYYY年M月D日。ts 为 0（无时间）返回空串。
 */
function formatUnreadTime(ts: number): string {
  if (!ts || !Number.isFinite(ts)) return '';
  const d = new Date(ts);
  const now = new Date();
  const sameDay = (a: Date, b: Date): boolean =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const pad = (n: number): string => String(n).padStart(2, '0');
  if (sameDay(d, now)) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameDay(d, yesterday)) return i18n.t('common.17');
  if (d.getFullYear() === now.getFullYear()) return i18n.t('common.dateShort', { m: d.getMonth() + 1, d: d.getDate() });
  return i18n.t('common.dateFull', { y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() });
}


/**
 * [v1.0 frameless] 窗口控制使用可聚焦、可键盘触发的真 <button>。
 * 外观统一交给 knowe-components.css：默认融入背景，悬停时仅做低调的明暗反馈。
 */

export const App: React.FC = () => {
  const { t } = useTranslation();
  // [阶段一 1.5] 正式版（打包安装版）判定：界面零开发痕迹。
  //   · ConnBadge / DevDrawer 是纯开发调试件 → 正式版直接不挂载；
  //   · BackendGate 刻意保持无条件挂载——它的「设置同步」副作用（后端 ready →
  //     reconcileFromBackend）正式版也必须执行，显示隐藏由组件内部按 isPackaged 处理。
  const isPackaged = window.knowe?.isPackaged === true;
  const activeView = useKnoweStore(selectActiveView);
  const activeId = useKnoweStore(selectActiveProjectId);
  const [rosterOpen, setRosterOpen] = useState(false);
  const [searchFocus, setSearchFocus] = useState<(GlobalSearchTarget & {
    requestId: number;
  }) | null>(null);
  const searchRequestId = useRef(0);

  const navigateFromSearch = useCallback((target: GlobalSearchTarget): void => {
    const requestId = searchRequestId.current + 1;
    searchRequestId.current = requestId;
    const store = useKnoweStore.getState();

    switch (target.kind) {
      case 'conversation':
        setSearchFocus(null);
        store.setView('chats');
        store.switchProject(target.projectId);
        break;
      case 'message':
        setSearchFocus({ ...target, requestId });
        store.setView('chats');
        store.switchProject(target.projectId);
        break;
      case 'contact':
        setSearchFocus({ ...target, requestId });
        store.setView('contacts');
        break;
      case 'favorite':
        setSearchFocus({ ...target, requestId });
        store.setView('favorites');
        break;
      case 'knowledge': {
        setSearchFocus(null);
        store.setView('knowledge');
        const knowledge = useKnowledgeStore.getState();
        const card = knowledge.cards.find((candidate) => candidate.id === target.cardId);
        if (card) knowledge.openPreview({ kind: 'asset', card, projectId: card.projectId });
        break;
      }
      case 'settings':
        setSearchFocus({ ...target, requestId });
        store.setView('settings');
        break;
      default:
        break;
    }
  }, []);

  const clearSearchFocus = useCallback((requestId: number): void => {
    setSearchFocus((current) => (
      current?.requestId === requestId ? null : current
    ));
  }, []);

  // [v1.0.24.1] 右键菜单「查看资料」→ 联系人资料页：走与全局搜索跳转同一通道。
  //   ContextMenu 在组件树深处，无法直接拿到 searchFocus setter——用 window 事件桥接：
  //   ContextMenu dispatch('knowe:focus-contact', {projectId, agentId}) → 这里转
  //   navigateFromSearch({kind:'contact'}) → ContactsView 直接 setSelected（含展开分组）。
  //   知知场景 projectId/agentId 为 null → ContactsView 回初始知知。
  useEffect(() => {
    const onFocusContact = (event: Event): void => {
      const detail = (event as CustomEvent<{ projectId?: string | null; agentId?: string | null }>).detail;
      if (!detail) return;
      navigateFromSearch({
        kind: 'contact',
        projectId: detail.projectId ?? null,
        agentId: detail.agentId ?? null,
      });
    };
    window.addEventListener('knowe:focus-contact', onFocusContact);
    return () => window.removeEventListener('knowe:focus-contact', onFocusContact);
  }, [navigateFromSearch]);

  /*
   * [v0.45.3] 聊天记录属于“当前会话 + 当前主视图”的瞬时覆盖层。
   * 任一导航身份发生变化，都必须在浏览器绘制新视图之前撤掉旧抽屉：
   *   · activeId 变化覆盖群聊 ↔ 群聊、群聊 ↔ DM、DM ↔ DM；
   *   · activeView 变化覆盖 Rail 上全部功能入口。
   *
   * 用 layout effect 而不是普通 effect：React 已经算出新视图、但还没 paint 时同步关抽屉，
   * 不会让旧会话的 RecordsDrawer 在新会话上闪一帧；抽屉自己的 closeDrawer 状态机和
   * CSS 开合动画仍是唯一关闭路径，不在各个头像/按钮里散落重复逻辑。
   */
  useLayoutEffect(() => {
    useRecordsStore.getState().closeDrawer();
    useTokenUsageStore.getState().closePanel();
    // [v1.0.23.5] 原 ChatStream「priv && rosterOpen 自动关花名册」上移至此：
    //   切到私聊（知知/DM）时关面板（私聊没有花名册）；群↔群切换保持面板状态。
    if (isPrivateChat(activeId)) setRosterOpen(false);
  }, [activeId, activeView]);

  /*
   * [v0.44 设置] 字号「大」仍是**根节点（.desk）上的一个类**：
   *   · fs-large —— settings-view.css 对主要文字载体逐类放大；
   *                 独立预览窗口拥有自己的根节点，不受主窗口字号类影响。
   *
   * [v1.0.19.2] 外观（深/浅）**不再挂在 .desk 上**：knowe-tokens.css 的深色变量
   *   定义在 `html.dark` 上，只有把类挂到 document.documentElement（<html>）才会生效。
   *   见下方 useEffect —— appearance 变化即在 <html> 上切换 dark / light。
   */
  const fontScale = useSettingsStore((s) => s.fontScale);
  const appearance = useSettingsStore((s) => s.appearance);
  const setAppearance = useSettingsStore((s) => s.setAppearance);
  const language = useSettingsStore((s) => s.language); // [v1.0.21.3] 界面语言

  /*
   * [v1.0.19.2] 深色令牌住在 `html.dark`：把类切到 <html>（document.documentElement），
   *   而不是 .desk div——否则 html.dark 变量表整个不生效。appearance 变则重挂。
   */
  useEffect(() => {
    document.documentElement.classList.toggle('dark', appearance === 'dark');
    document.documentElement.classList.toggle('light', appearance !== 'dark');
  }, [appearance]);

  /*
   * [v1.0.21.3 修复] 界面语言跟随 store：启动时 persist 恢复 / reconcileFromBackend
   *   对账 / 手动 Apply 都会改 store.language，这里统一同步 i18n。
   *   此前 changeLanguage 只在 PrimaryLanguageModule 手动切换时调用——
   *   设置里持久化的是 en，启动后 i18n 仍是默认 zh，界面中文、设置英文，两边对不上。
   */
  useEffect(() => {
    void i18n.changeLanguage(language);
  }, [language]);

  /*
   * [v1.0.19.2] 首次启动跟随系统偏好：仅当用户从未手动改过外观（仍是默认 light）时，
   *   若系统处于深色则切到深色。只跑一次；此后以用户手动选择为准。
   */
  useEffect(() => {
    if (appearance === 'light'
        && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      setAppearance('dark');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /*
   * [v1.0.13 R1] 后端持久设置是运行时真源。启动时先 GET 对账；只有“本地有封存
   * binding、后端为空”的首次安装分支才由 settings store 向后端补交快照，而且不会冒充
   * 已完成 test→apply 激活。
   */
  useEffect(() => {
    void useSettingsStore.getState().reconcileFromBackend();
  }, []);

  /*
   * [v0.8c #5] 私聊（知知）**根本不挂花名册面板** —— 连那个 0 宽度的壳都不挂。
   *
   *   跟一个人的对话框旁边摆一张「本群成员」的名单，是没有意义的。
   *   （宽度的事另有一手：.roster-wrap 现在是浮在聊天区上面的抽屉，
   *     开合都不再挤压聊天区 —— 见 CSS #5。两手一起，切群时宽度是个常数。）
   */
  const privateChat = isPrivateChat(activeId);

  // [v1.0.23.5] 全局覆盖层（聊天记录抽屉 / Token 统计）上提到 .chat-card 直接子级：
  //   会话常驻后由活动会话 id 决定展示哪个会话的覆盖层。
  //   ★ RecordsDrawer 必须外部条件渲染：.drawer-wrap 的 base 样式本就铺满 .chat-card
  //     （inset:0 + 不透明背景），原实现靠 open===true 才挂载 DOM；无条件挂载会常驻遮挡。
  const recordsOpen = useRecordsStore((s) => s.open);
  const recordsSessionId = activeId || '';
  const recordsIsGroup = !isPrivateChat(activeId) && activeId !== PLATFORM_PROJECT_ID;

  /*
   * [v0.8d #5] 未读 → 左栏红点（ConvList 自己读 store）+ 任务栏闪烁（这儿转给主进程）。
   *
   *   总数在这里算一次就够了：红点是每个会话自己的事，闪烁是「整个应用有没有人在等你」。
   *   **一处记账（conv.unread），两处显示** —— 不会出现「红点没了但任务栏还在闪」。
   */
  const totalUnread = useKnoweStore(
    (s) => Object.values(s.convs).reduce((n, c) => n + (c.unread || 0), 0),
  );

  useEffect(() => {
    // 老 preload 上没有这个方法（桥是这一版才加的）→ 用 ?. 调，没有就算了，不能崩。
    window.knowe?.setUnread?.(totalUnread);
  }, [totalUnread]);

  /*
   * [v1.0.19.1] 托盘悬停卡片需要知道「谁有未读、各几条」——不只是一个总数。
   *
   *   知知（PLATFORM_PROJECT_ID）也是左栏里的一个会话，她说了话你也该看见，
   *   所以这里**不排除**她，和上面 totalUnread、以及 ConvList 里「知知也算」的
   *   既有口径保持一致（一处记账，处处一致口径，不出现「左栏有红点，托盘卡片却没她」）。
   *
   *   selector 本身每次 store 变化都会算出一个新数组（浅比较对象数组意义不大），
   *   所以真正决定要不要推给主进程的，是下面序列化后的 unreadDetailsKey——只有
   *   「谁有未读 / 各几条 / 最后一条消息」真的变了，才发一次 IPC，避免任何无关的
   *   store 变化（打字、流式消息……）都触发一次跨进程通信。
   *
   *   [v1.0.20.2] 明细扩展到消息级：每行取该会话**最后一条消息**的发送者、预览、
   *   时间、头像（托盘卡片按权威参考重做，显示消息行而不是光秃秃的项目名）。
   *   头像 URL 拼成完整地址——托盘卡片是独立窗口（data: URL），相对路径解析不到。
   */
  const unreadDetails = useKnoweStore((s) =>
    Object.entries(s.convs)
      .filter(([, c]) => (c.unread || 0) > 0)
      .map(([id, c]): UnreadDetail => {
        const items = c.items;
        const last = items[items.length - 1];
        let sender = '';
        let memberName = '';
        let memberRole = '';
        let avatarUrl = '';
        let ts = 0;
        if (last) {
          ts = 'ts' in last ? (last.ts || 0) : 0;
          if (last.kind === 'agent') {
            const m = c.members.find((x) => x.id === last.agentId);
            memberName = m ? memberNameLabel(m.id, m.display.name) : '';
            memberRole = roleLabel(m?.display.role || '');
            avatarUrl = m?.display.avatarUrl || '';
          } else if (last.kind === 'user') {
            memberName = '我';
          }
          sender = memberName;
        }
        // 预览：纯文本去换行，超长截断（CSS 还有一行省略号兜底）
        const rawText = last && 'text' in last ? last.text : '';
        const oneLine = rawText.replace(/\s+/g, ' ').trim();
        const preview = oneLine.length > 80 ? `${oneLine.slice(0, 80)}…` : oneLine;
        return {
          projectId: id,
          projectName: c.projectName || id,
          unread: c.unread || 0,
          sender,
          memberName,
          memberRole,
          preview,
          timeLabel: formatUnreadTime(ts),
          ts,
          // [v1.0.25.3] 不拼 window.location.origin：打包版是 file://，拼出来是
          //   file://./avatars/... 无效 URL（pop 卡片头像加载失败实锤）。
          //   直接传相对路径，trayCard 的 resolveAvatarUrl 统一处理（读文件转 base64 内联）。
          avatarUrl: avatarUrl || '',
        };
      })
      .sort((a, b) => b.ts - a.ts));
  const unreadDetailsKey = JSON.stringify(unreadDetails);
  useEffect(() => {
    window.knowe?.setUnreadDetails?.(unreadDetails);
    // unreadDetailsKey 是真正的门槛：数组引用每次都新，但内容没变就不必再发一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unreadDetailsKey]);

  /*
   * [v1.0.19.1] 托盘卡片点了某一行 → 主进程已经把窗口拉到前台，这里只管切会话。
   * 老 preload 上没有这个方法 → 用 ?. 调，返回 undefined 时不订阅也不能崩。
   */
  useEffect(() => {
    const cleanup = window.knowe?.onTrayCardNavigate?.((projectId: string) => {
      useKnoweStore.getState().switchProject(projectId);
    });
    return () => { cleanup?.(); };
  }, []);

  // 窗口在不在前台：失焦的时候，连「当前这个群」的新消息也算没看见——你人不在。
  useEffect(() => {
    const st = useKnoweStore.getState();
    const onFocus = (): void => st.setWindowFocused(true);
    const onBlur = (): void => st.setWindowFocused(false);
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    st.setWindowFocused(document.hasFocus());
    return () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('blur', onBlur);
    };
  }, []);

  /*
   * [v0.4] 知知：软件一打开，第一个接待你的人。
   *
   * 用户第一次进来时不该看到一片空白——他还没有任何项目，空白页什么忙也帮不上。
   * 所以启动时就把知知的会话建好，并且默认停在她那儿：一开门就有人问「想做点什么」。
   */
  useEffect(() => {
    const st = useKnoweStore.getState();
    st.ensureProject(PLATFORM_PROJECT_ID, getZinniaDisplayName());
    if (!st.activeProjectId) st.switchProject(PLATFORM_PROJECT_ID);
  }, []);
  // ── socket 生命周期（全应用唯一一次；模块级标记防重，见文件头注释） ──
  useEffect(() => {
    if (_appSocketBooted) return; // 防重复挂载（StrictMode / HMR / remount）
    _appSocketBooted = true;

    const store = useKnoweStore.getState();

    /*
     * [v0.13 卡片] 项目目录失效仍是服务器级控制流（不进聊天时间线、不占 seq 水位），
     *   但呈现方式从「Electron 原生目录弹窗」改成「聊天区顶部的目录恢复卡片」：
     *     · project_directory_required → 目录 store openRequest（弹出卡片，5 分钟倒计时）
     *     · project_directory_restored → 目录 store resolve（清掉红字与卡片）
     *     · project_created 里若带 directory_required（重连/冷启动）→ syncFromProjectCreated
     *       （先亮侧边栏红字，卡片留给用户点开）
     *   卡片交互（确认/取消/超时）通过 store 里绑定的 socket 走 set/cancel_project_directory 回后端，
     *   所以这里把 socket 注入目录 store（镜像下面的 store.setSocket）。
     */
    const socket = createSocket({
      callbacks: {
        onEvent: (ev) => {
          // Token 查询响应是旁路控制帧：先消费，绝不进入聊天时间线或未读状态机。
          if (handleTokenUsageEvent(ev as unknown)) return;
          // project_created 先喂给目录 store（它只读 directory_required 字段），再照常入主状态机。
          if (ev.type === 'project_created') syncDirectoryFromProjectCreated(ev);
          // [v1.0.24.6-P2] 事件批量消费：入 16ms rAF 窗口队列，每帧最多渲染一次
          //（详见模块级 enqueueEvent/flushEvents 注释）。
          enqueueEvent(ev);
        },
        onStatus: (st) => useKnoweStore.getState().setConnStatus(st),
        onEchoOk: (cmid) => useKnoweStore.getState().confirmEcho(cmid),
        onEchoLost: (cmid, pid) => {
          useKnoweStore.getState().suspectEcho(cmid);
          useKnoweStore.getState().addNotice(t('app.08'));
          recordDiagnostic({                       // [v0.3-走廊] 哨兵告警要留痕
            dir: 'out', type: 'user_message', projectId: pid, verdict: 'sentinel',
            summary: `回声超时 5s（cmid=${cmid}）→ 疑似广播失聪`,
          });
        },
        onEpochReset: (pid) => {
          useKnoweStore.getState().clearProject(pid);
          useKnoweStore.getState().addNotice(t('app.07'));
        },
        onProjectDiscovered: (pid, name) => useKnoweStore.getState().ensureProject(pid, name),
        // [v1.0.24.4] replay_complete 携带权威活动账本 → store 校准花名册忙碌状态
        onActivityLedger: (pid, activity) =>
          useKnoweStore.getState().calibrateActivity(pid, activity),
        getActiveProjectId: () => useKnoweStore.getState().activeProjectId,
        // [v0.13 卡片] 目录失效 → 弹出聊天区内恢复卡片（不再开原生目录对话框）。
        onProjectDirectoryRequired: (event) => openDirectoryRequest(event),
        // [v0.13 卡片] 目录已恢复 → 清掉该项目的红字与卡片。
        onProjectDirectoryRestored: (event) => {
          resolveDirectory(event.project_id);
          useKnoweStore.getState().addNotice(event.message || t('app.10'));
        },
        // [v0.3-走廊] 传输层的每一次丢弃/拒收/失败，都在走廊里留一条
        onDiagnostic: recordDiagnostic,
      },
    });

    store.setSocket(socket);
    bindTokenUsageSocket(socket);
    // 卡片确认/取消/超时经目录 store 调这两个命令回后端。
    bindDirectorySocket(socket);

    // [v1.0.23.6] 启动预热：HTTP 增量（读本地骨架水位 → 拉新事件 → 纯注入 store）。
    //   WS 快照仍是最终基准（到达后整体重建覆盖）；增量只是让重启后第一次点开
    //   群「先有内容 + 行高/滚动原位」，消除 2-3 秒空白→乱跳→恢复。
    //   静默失败：接口/骨架任何异常都只是慢，不是错（快照兜底）。
    void warmUpIncremental();

    // [v1.0.18.4] 首次连接前先取回并缓存 Runtime Token。WebSocket 无法自定义 header，
    // token 通过 URL query 注入（见 socket.ts getAuthWsUrl）；initWsAuthToken() 会把
    // token 写入模块级 authTokenCache，之后 scheduleReconnect 的重连无需再走 IPC。
    // 用 async IIFE 而非把 effect 改成 async——后者会让 cleanup 返回 Promise 而失效。
    let cancelled = false;
    void (async () => {
      await initWsAuthToken();
      if (!cancelled) socket.connect();
    })();

    return () => {
      // [v1.0.23.2] 不再 disconnect / 不再重置标记：socket 是全应用单实例，
      //   App remount 时连接保持（否则 HMR 每次更新都重建连接 + 全量重放）。
      cancelled = true;
    };
  }, []);

  const chatSearchJump = searchFocus?.kind === 'message' ? searchFocus : null;
  const contactsSearchFocus = searchFocus?.kind === 'contact' ? searchFocus : null;
  const favoritesSearchFocus = searchFocus?.kind === 'favorite' ? searchFocus : null;
  const settingsSearchFocus = searchFocus?.kind === 'settings' ? searchFocus : null;

  return (
    <>
      <div
        className={
          'desk'
          + (fontScale === 'large' ? ' fs-large' : '')
        }
      >
        <div className="win">

          {/*
            [v1.0 frameless] 原生标题栏随 frame:false 去除（main.ts），这里保留紧凑拖拽区：
              · .titlebar / .title-mid 负责拖动窗口，纵向只占控制按钮所需高度；
              · .traffic 为 no-drag，按钮按 Windows 常规顺序排列：最小化、最大化/还原、关闭；
              · 动作仍走 invokeWindowControl → preload 桥 → 主进程窗口 API，控制逻辑不变。
            Logo 仍在 Rail，BackendGate/ConnBadge 仍在右下 .floatbar。
          */}
          <header className="titlebar">
            <div className="title-mid" />
            <div className="traffic">
              <button
                type="button"
                className="tl tl-minimize"
                title={t('app.05')}
                aria-label={t('app.06')}
                onClick={() => invokeWindowControl('minimize')}
              />
              <button
                type="button"
                className="tl tl-maximize"
                title={t('app.03')}
                aria-label={t('app.04')}
                onClick={() => invokeWindowControl('toggle-maximize')}
              />
              <button
                type="button"
                className="tl tl-close"
                title={t('app.01')}
                aria-label={t('app.02')}
                onClick={() => invokeWindowControl('close')}
              />
            </div>
          </header>

          {/* ═══ 应用主体 ═══ */}
          <div className="app">
            <Rail />

            <div className="views">
              {/* 项目视图 */}
              <div className={'view view-chats' + (activeView === 'chats' ? ' active' : '')}>
                <ConvList onSearchNavigate={navigateFromSearch} />
                <main className="main">
                  <section className="chat-card">
                    {activeId ? (
                      <>
                        {/*
                          [v0.13 卡片位置修正] 目录恢复卡片**不再**钉在 .chat-card 顶部（那会跑到
                          群聊名称上方，很怪）。它现在渲染在 ChatStream 的消息流里、群聊头部下方，
                          和知知的建群卡、项目经理的拉人卡/派活卡同一个位置——见 ChatStream.tsx。
                        */}
                        {/* [v1.0.23.5] 会话视图常驻内存：SessionHost 管理全部会话实例，常驻显隐 */}
                        <SessionHost
                          rosterOpen={rosterOpen}
                          onToggleRoster={() => setRosterOpen((v) => !v)}
                          searchJump={chatSearchJump}
                          onSearchJumpDone={clearSearchFocus}
                        />
                        <Composer />
                        {/* [v1.0.23.5] 全局覆盖层：随活动会话对齐，.chat-card 直接子级（定位祖先不变）
                            RecordsDrawer 外部条件渲染（open 才挂载，见上注释）；TokenUsagePanel 内部自理 */}
                        {recordsOpen && (
                          <RecordsDrawer
                            projectId={recordsSessionId}
                            isGroup={recordsIsGroup}
                          />
                        )}
                        <TokenUsagePanel />
                      </>
                    ) : (
                      <EmptyState />
                    )}
                  </section>
                  {!privateChat && (
                    <RosterPanel open={rosterOpen} onClose={() => setRosterOpen(false)} />
                  )}
                </main>
              </div>


              {/*
                副视图容器。
                [v0.39] 「联系人」已接入：activeView==='contacts' 时挂 ContactsView（.side + .stage
                  是 .view-alt 的 flex 兄弟，正好铺满这一栏）。副视图都按 activeView 分流，
                  绝不能整栏无条件替换成联系人页，
                  否则点「收藏」也会看到联系人。
              */}
              <div className={'view view-alt' + (activeView !== 'chats' ? ' active' : '')}>
                {/* [v0.40.0] 「收藏」接入：和联系人一样按 activeView 分流。 */}
                {/* [v0.41]   「知识库」接入：同款 .side + .stage 骨架，数据来自后端知识图谱
                             （store/knowledge.ts → knowledge_api.py，禁 mock）。 */}
                {/* [v0.44]   「设置」接入：同款 .side + .stage 骨架（.set-nav + .set-pane），
                             五个分区（账户与身份 / 模型与提供方 / 通知 / 外观 / 关于）。 */}
                {activeView === 'contacts' ? (
                  <ContactsView
                    searchFocus={contactsSearchFocus}
                    onSearchFocusDone={clearSearchFocus}
                  />
                ) : activeView === 'favorites' ? (
                  <FavoritesView
                    searchFocus={favoritesSearchFocus}
                    onSearchFocusDone={clearSearchFocus}
                  />
                ) : activeView === 'knowledge' ? (
                  <KnowledgeView />
                ) : activeView === 'settings' ? (
                  <SettingsView
                    searchFocus={settingsSearchFocus}
                    onSearchFocusDone={clearSearchFocus}
                  />
                ) : (
                  <div className="empty2">
                    <div className="e-ic" />
                    <p>{t('app.09')}</p>
                  </div>
                )}
              </div>
            </div>
          </div>

        </div>
      </div>

      <CommandPalette onNavigate={navigateFromSearch} />

      {/* [v0.5 #8] 标题栏没了，这两个得有地方待——右下角悬浮。
          平时不占地方（后端正常时 BackendGate 什么都不渲染）。 */}
      <div className="floatbar">
        {/* [阶段一 1.5] BackendGate 正式版也保持挂载（保留「设置同步」副作用），显示隐藏由组件内部处理；
            ConnBadge 是纯开发调试件，正式版不挂载（组件内部亦有同款判定，双保险）。 */}
        <BackendGate />
        {!isPackaged && <ConnBadge />}
      </div>
      <ToastHost />
      {/* [v0.40.0] 右键菜单 / 转发弹窗 / 确认弹窗 / 操作 toast 的浮层宿主（portal 到 body）。
          与 ToastHost 分工：ToastHost 播全局 notices；这里播菜单操作的轻反馈（已复制/已收藏…）。 */}
      <FloatingLayers />
      {/* [阶段一 1.5] DevDrawer 是开发走廊抽屉（Ctrl+Shift+D），正式版不挂载（组件内部亦有同款判定）。 */}
      {!isPackaged && <DevDrawer />}
      {/*
        [v0.44.1 Bug3] 首次启动·强制模型引导。挂在最后 = z 序最高：还没配置全局主模型时
        铺一层雾化遮罩把整屏（含浮层/DevDrawer）罩住，走完「选模型→连接测试→进入」才放行。
      */}
      <FirstRunModelGate />
    </>
  );
};

export default App;
