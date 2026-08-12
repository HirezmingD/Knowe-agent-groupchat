/**
 * ContactsView.tsx — 联系人视图（v0.39 → v0.39.1）
 *
 * 挂载点：App.tsx 的 .view-alt（activeView === 'contacts' 时渲染本组件）。
 * 布局：左 .side（联系人列表）+ 右 .stage（选中联系人的资料页）。
 *
 * ── v0.39.1 六项修复在本文件中的落点 ──
 *
 *   #1 选中态：删掉 SELECTED_BAR（左侧蓝边线），改为 `.navrow.selected` 类 →
 *      knowe-components.css 里新增的整行底色（比 hover 的 surface-2 深一档）。
 *
 *   #2 资料页排版统一：所有资料页头部（知知 / 项目经理 / Worker / 群）走**同一条渲染路径**——
 *      `.pf-top > <Avatar size={44}>`，由 CSS 统一放大到 64px。根因是 v0.39 传了
 *      size={64}：AvatarSize 只有 28~44，`av-64` 类不存在 → 头像尺寸被**图片的固有
 *      分辨率**接管，而项目经理池（/avatars/Coordinator/）和成员池（/avatars/agent/）
 *      的图不一样大 → 项目经理的头看起来就和别人不一样。项目经理与 Worker 之间的文案差异
 *      （副标题 / 档案 / 权限）全部来自**按角色查表的数据**，渲染结构零分支。
 *
 *   #3 群聊头像：删掉自造的 groupFace()，改用聊天列表同一套宫格组件 AvatarGrid
 *      （Avatar.tsx，[v0.5b #6]），并按它的既定约定喂数据：归档滤掉、项目经理排第一、
 *      截断到 9 由组件自己做。左栏列表与群资料页头部用的都是它。
 *
 *   #4 实时联动：右侧资料页不再由父组件传对象快照，改为**按 id 自行订阅 store**
 *      （s.convs[projectId]）——immer 之下只有本项目变化才触发重渲。成员数、成员
 *      列表、宫格、当前状态（含「已归档」）全部随事件即时刷新。运行参数搬进独立的
 *      useAgentParamsStore（agentParams.ts）：今天是本地态，接后端时组件零改动。
 *
 *   #5 协作权限：知知 / 项目经理 / Worker 三类角色分别给出「沙箱 + 记忆 + 工具」的
 *      综合权限说明（PERMISSION_* 模板，语义对齐代码里的既有约定：projectDir 是
 *      Worker 沙箱根、私聊记忆写回项目且项目经理可见、关键操作走审批卡）。
 *
 *   #6 擅长领域：全部改成一句完整的自然语言描述。Worker 按 24 个已知角色逐一
 *      成文（SKILL_BY_ROLE，与  的 label 一一对应，改一头必须
 *      改另一头），库外角色走兜底模板；知知与项目经理各有专属句子。
 *
 * ── 三条老铁律（v0.39 起，继续成立）──
 *
 *   ① 头像单一数据源：`m.display.avatarUrl ?? faceFor(id, projectId, projectName)
 *      .avatarUrl`，与 ChatStream 逐字相同；宫格的脸也从这同一条式子来。
 *   ② 进聊天只走既有入口：switchProject / enterDm / RecordsDrawer 的 openDrawer，
 *      切走前 setView('chats')。
 *   ③ 不碰聊天区的任何状态：.view-chats 只是被 CSS 藏起，草稿、滚动位置都在。
 */

import React, { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { roleLabel, memberNameLabel } from '../shared/roleLabel';
import { useKnoweStore } from '../store/store';
import type { Conv, Member } from '../store/state';
import { Avatar, AvatarGrid, type GridMember } from './Avatar';
import {
  faceFor, isCoordinator,
  ZINNIA_AVATAR, getZinniaDisplayName, PLATFORM_PROJECT_ID,
} from '../store/avatar';
import { isPrivateChat } from '../store/chat';
import { useRecordsStore } from '../store/records';
// [v0.44 §3.1/§3.2] 运行参数（温度/最大迭代）随本版本退役，agentParams store 不再引用；
//   per-Agent 的模型独立设置改由全局设置 store 承载（与设置页同一数据源、同一交互）。
import {
  useSettingsStore, agentBindingKey, effectiveGroupTimeout, APPROVAL_TIMEOUT_OPTIONS,
} from '../store/settings';
import ModelBindingModule from './ModelBindingModule';
import MSetSelect from './MSetSelect'; // [v1.0.24.1] 审批卡超时下拉统一为 mset 样式
import { openContactAgentMenu, openContactGroupMenu, toast } from './ContextMenu';

// ═══════════════════════════════════════════════════════════════
// 图标（内联 SVG，取自设计参考的 ICON 表，与全局 lucide 风格一致：stroke 1.5、圆角端点）
//   —— 刻意不从 ./icons import：那份清单里未必导出 msg/report 这些，内联最稳、零外部依赖。
// ═══════════════════════════════════════════════════════════════

type IcProps = { size?: number };
const svg = (size: number, children: React.ReactNode): React.ReactElement => (
  <svg
    width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round"
  >
    {children}
  </svg>
);

const IcSearch: React.FC<IcProps> = ({ size = 15 }) => svg(size, (<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" /></>));
const IcChevDown: React.FC<IcProps> = ({ size = 16 }) => svg(size, <path d="m6 9 6 6 6-6" />);
const IcMsg: React.FC<IcProps> = ({ size = 20 }) => svg(size, <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />);
const IcReport: React.FC<IcProps> = ({ size = 20 }) => svg(size, (<><path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" /><path d="M14 3v4h4M8.5 13h7M8.5 16.5h5" /></>));
const IcUsers: React.FC<IcProps> = ({ size = 15 }) => svg(size, (<><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>));
// [v0.39.3] 项目目录用的文件夹图标（线条 + 圆角，与其它图标同一 stroke 1.5 风格）
const IcFolder: React.FC<IcProps> = ({ size = 14 }) => svg(size, <path d="M3 7.5a2 2 0 0 1 2-2h3.2a2 2 0 0 1 1.4.6l.8.8a2 2 0 0 0 1.4.6H19a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />);

// ═══════════════════════════════════════════════════════════════
// [v0.39.3] 打开项目根目录（系统默认文件管理器）
//
//   Electron：走 preload 暴露的桥（contextIsolation 下的标准做法）——不同项目命名不一，
//     常见的几种都探一遍；老式 nodeIntegration 直连 require('electron').shell 也兜底。
//   浏览器 / 纯前端开发模式：静默空操作，绝不抛错（本函数任何一步失败都吞掉）。
//
//   ⚠ 若你的 preload 暴露的方法名不在下面探测之列，把这一个函数指过去即可（改一行）。
//     语义等价于 README 要求的 `shell.openPath(path)`。
// ═══════════════════════════════════════════════════════════════

type OpenPathFn = (p: string) => unknown;

function resolveOpenPath(): OpenPathFn | null {
  const w = window as unknown as {
    knowe?: { openPath?: OpenPathFn; openFolder?: OpenPathFn };
    electron?: { shell?: { openPath?: OpenPathFn }; openPath?: OpenPathFn };
    electronAPI?: { openPath?: OpenPathFn; shell?: { openPath?: OpenPathFn } };
    require?: (m: string) => { shell?: { openPath?: OpenPathFn } };
  };
  const bridge =
    w.knowe?.openPath ?? w.knowe?.openFolder ??
    w.electron?.shell?.openPath ?? w.electron?.openPath ??
    w.electronAPI?.openPath ?? w.electronAPI?.shell?.openPath;
  if (typeof bridge === 'function') return bridge;
  // nodeIntegration 直连（部分开发配置）
  const shellOpen = w.require?.('electron')?.shell?.openPath;
  if (typeof shellOpen === 'function') return shellOpen;
  return null;
}

function openProjectFolder(dir?: string): void {
  if (!dir) return;
  try {
    const open = resolveOpenPath();
    if (open) open(dir);   // 未注入桥（浏览器）→ open 为 null → 什么都不做
  } catch {
    /* 降级为空操作，不打断 UI */
  }
}

/**
 * 「项目目录」标题后面那个可点的文件夹图标。
 *   · 有路径：可点击 + hover 变主题色，点击打开项目根目录。
 *   · 无路径：灰掉、不可点（openProjectFolder 也会因空路径直接返回）。
 * 纯内联样式，不新增全局 CSS——本轮只动 ContactsView.tsx 一个文件。
 */
const FolderButton: React.FC<{ dir?: string }> = ({ dir }) => {
  const { t } = useTranslation();
  const [hover, setHover] = useState(false);
  const enabled = !!dir;
  const activate = (): void => openProjectFolder(dir);
  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={t('contacts.view.47')}
      title={enabled ? t('contacts.view.61') : t('contacts.view.54')}
      onClick={activate}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', verticalAlign: 'middle',
        marginLeft: 5, flexShrink: 0,
        cursor: enabled ? 'pointer' : 'default',
        opacity: enabled ? 1 : 0.4,
        color: enabled && hover ? 'var(--accent)' : 'var(--ink-3)',
        transition: 'color var(--dur-micro)',
      }}
    >
      <IcFolder />
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════
// 折叠容器：measured-height 版 max-height 动画（对应 .sec-body / .drill 的 CSS transition）。
//
//   为什么要测量而不是写死一个大 max-height：写死会让「短内容」也要跑完整段区间，节奏发飘；
//   而且分组里嵌着可展开的下钻（drill），外层 max-height 得随内层撑开——测量 + 展开后置 'none'
//   两手一起，嵌套展开才不会被外层裁掉（'none' 让内容自然生长）。收起时反向：从当前高度 → 0。
// ═══════════════════════════════════════════════════════════════

const COLLAPSE_MS = 460; // ≥ --dur-soft(420ms)，保证展开动画跑完再置 'none'

const Collapse: React.FC<{ open: boolean; className?: string; children: React.ReactNode }> = ({
  open, className, children,
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const [maxH, setMaxH] = useState<string>(open ? 'none' : '0px');
  const mounted = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 首帧不做动画：直接落到目标态（初始展开的分组不该「长」出来一次）。
    if (!mounted.current) {
      mounted.current = true;
      setMaxH(open ? 'none' : '0px');
      return;
    }
    window.clearTimeout(timer.current);
    if (open) {
      const target = el.scrollHeight;
      setMaxH('0px');
      requestAnimationFrame(() => {
        setMaxH(target + 'px');
        timer.current = window.setTimeout(() => setMaxH('none'), COLLAPSE_MS);
      });
    } else {
      const current = el.scrollHeight;
      setMaxH(current + 'px'); // 先把 'none' 固化成具体像素，才能过渡到 0
      requestAnimationFrame(() => setMaxH('0px'));
    }
    return () => window.clearTimeout(timer.current);
  }, [open]);

  return <div ref={ref} className={className} style={{ maxHeight: maxH }}>{children}</div>;
};

// ═══════════════════════════════════════════════════════════════
// 脸 / 名 / 角色 —— 全部从「花名册成员的 display」取，与聊天区完全一致
// ═══════════════════════════════════════════════════════════════

interface Face { glyph: string; pal: string; src?: string; name: string; role: string; }

/** 群内成员的脸：display 优先，缺 avatarUrl 时按 faceFor 的项目种子兜底——和 ChatStream 同一条式子。 */
function memberFace(conv: Conv, m: Member): Face {
  const src = m.display.avatarUrl ?? faceFor(m.id, conv.projectId, conv.projectName).avatarUrl;
  return { glyph: m.display.glyph, pal: m.display.pal, src, name: memberNameLabel(m.id, m.display.name), role: roleLabel(m.display.role) };
}

/** 知知的脸：固定头像、固定名，角色「接待」。 */
const ZINNIA_FACE_STATIC = { glyph: '知', pal: 'av-a', src: ZINNIA_AVATAR } as const;
function zinniaFace(t: (k: string) => string): Face {
  return { ...ZINNIA_FACE_STATIC, name: getZinniaDisplayName(), role: t('common.16') };
}

/** 只统计「在队」的成员（已归档的不算——和聊天区标题、花名册三处口径一致）。 */
function liveMembers(conv: Conv): Member[] {
  return (conv.members || []).filter((m) => m.status !== 'removed');
}

/**
 * [v0.39.1 #3] 群聊宫格的喂料——按 AvatarGrid（Avatar.tsx [v0.5b #6]）的既定约定：
 *   · 归档的人滤掉（「现在这个群里有谁」）
 *   · 项目经理永远排第一（左上），其余保持花名册顺序（Array.sort 稳定）
 *   · 截断到 9 由组件自己做，这里不重复
 * 每张脸都走 memberFace 的同一条式子 → 与聊天列表、气泡、花名册完全同源；
 * 成员增删后 conv.members 变化 → 订阅方重渲 → 宫格立刻跟着变（#4）。
 */
function gridMembersOf(conv: Conv): GridMember[] {
  const sorted = [...liveMembers(conv)].sort(
    (a, b) => Number(isCoordinator(b.id)) - Number(isCoordinator(a.id)),
  );
  return sorted.map((m) => {
    const f = memberFace(conv, m);
    return { id: m.id, glyph: f.glyph, pal: f.pal, avatarUrl: f.src };
  });
}

// ═══════════════════════════════════════════════════════════════
// [v0.39.1 #5] 协作权限 —— 三类角色的「沙箱 + 记忆 + 工具」综合说明。
//
//   文案不是随手编的，语义对齐代码里的既有约定：
//     · Conv.projectDir：「项目目录（Worker 沙箱的根）」（state.ts [v0.7 A0]）
//     · chat.ts [v0.37]：私聊记忆写回所属项目，「项目经理始终知道用户和每个成员私下聊了什么」
//     · 增删成员 / 分派任务走审批卡（propose_agents → 用户确认）
//     · 知知是平台级接待，不住在任何项目里（avatar.ts / App.tsx [v0.4]）
// ═══════════════════════════════════════════════════════════════

export const permissionZinnia = () =>
  i18n.t('contacts.view.57')
  + i18n.t('contacts.view.36');

export const permissionCoordinator = () =>
  i18n.t('contacts.view.97')
  + i18n.t('contacts.view.44');

export const permissionWorker = () =>
  i18n.t('contacts.view.96')
  + i18n.t('contacts.view.37');

function permissionOf(m: Member): string {
  return isCoordinator(m.id) ? permissionCoordinator() : permissionWorker();
}

// ═══════════════════════════════════════════════════════════════
// [v0.39.1 #6] 擅长领域 —— 一句完整的自然语言描述，不再是标签词堆砌。
//
//   ★ SKILL_BY_ROLE 的键 =  的 label（24 个），与 state.ts /
//     后端 KNOWN_ROLES 三方对齐的那套角色库一一对应——**改一头必须改另一头**
//     （CHANGES 附带的 check-skill-coverage 脚本会核对这份映射是否漏了角色）。
//   查找顺序：角色 label 精确命中 → id 前缀（fe_1 → 'fe' → '前端'）→ 兜底模板。
// ═══════════════════════════════════════════════════════════════

export const skillZinnia = () =>
  i18n.t('contacts.view.70');

export const skillCoordinator = () =>
  i18n.t('contacts.view.84');

/**
 * 24 个已知角色（键与 .label 一致）→ 一句成文的擅长领域。
 *
 * [v0.39.2 #1] 每句都收敛到该职能的真实工作场景 / 核心能力 / 产出特征，带上这一行
 *   才会用到的具体术语（如前端的「状态流转」、后端的「事务与缓存一致性」、SRE 的
 *   「错误预算」、DBA 的「慢查询与分库分表」），不再是能安到任何角色头上的口号。
 */
export const SKILL_BY_ROLE: Record<string, string> = {
  fe: 'contacts.view.69',
  be: 'contacts.view.88',
  pm: 'contacts.view.67',
  qa: 'contacts.view.39',
  ux: 'contacts.view.65',
  da: 'contacts.view.38',
  devops: 'contacts.view.62',
  sec: 'contacts.view.40',
  ml: 'contacts.view.45',
  mobile: 'contacts.view.48',
  game: 'contacts.view.68',
  gis: 'contacts.view.51',
  mkt: 'contacts.view.46',
  fin: 'contacts.view.79',
  hc: 'contacts.view.50',
  edu: 'contacts.view.83',
  ar: 'contacts.view.76',
  sup: 'contacts.view.94',
  sre: 'contacts.view.81',
  db: 'contacts.view.87',
  arch: 'contacts.view.86',
  writer: 'contacts.view.66',
  media: 'contacts.view.63',
  legal: 'contacts.view.53',
};

function skillOf(m: Member): string {
  if (isCoordinator(m.id)) return skillCoordinator();
  // 键 =  的 type（fe/be/...），语言无关；值 = i18n key，渲染时求值。
  const type = m.id.split('_')[0] ?? '';
  const byType = SKILL_BY_ROLE[type];
  if (byType) return i18n.t(byType);
  const role = (roleLabel(m.display.role) || '').trim();
  // 库外角色（KNOWN_ROLES 之外，正常不该出现）：仍给一句成句描述，别让资料页开天窗。
  // 注意不套用「在项目中承担 XX 方向的工作」这类被点名的万能模板（[v0.39.2 #1]）。
  return i18n.t('contacts.view.roleDesc', { role: role || i18n.t('contacts.view.89') });
}

// 资料页里的「职责描述」：production 没有 per-agent 的 SOUL 字段，按角色合成一句像样的。
function agentSoul(conv: Conv, m: Member): string {
  const proj = conv.projectName || conv.projectId;
  if (isCoordinator(m.id)) {
    return i18n.t('contacts.view.leadDesc', { proj });
  }
  return i18n.t('contacts.view.workerDesc', { proj, role: roleLabel(m.display.role) || i18n.t('contacts.view.82') });
}

/** 副标题（#2：文案差异全部来自数据，渲染结构对项目经理零分支）。 */
function subtitleOf(conv: Conv, m: Member): string {
  const proj = conv.projectName || conv.projectId;
  return isCoordinator(m.id)
    ? i18n.t('contacts.view.leadTag', { proj })
    : i18n.t('contacts.view.workerTag', { proj, role: roleLabel(m.display.role) || 'Agent' });
}

/** 当前状态（#4：随 store 实时切换；归档态一并覆盖）。 */
function stateLabelOf(m: Member): string {
  if (m.status === 'removed') return i18n.t('contacts.view.12');
  return m.state === 'busy' ? i18n.t('contacts.view.11') : i18n.t('contacts.view.24');
}

// ═══════════════════════════════════════════════════════════════
// 选择态
// ═══════════════════════════════════════════════════════════════

type Selection =
  | { kind: 'empty' }
  | { kind: 'zinnia' }
  | { kind: 'group'; projectId: string }
  | { kind: 'agent'; projectId: string; agentId: string };

const selKey = (s: Selection): string =>
  s.kind === 'empty' ? 'empty'
    : s.kind === 'zinnia' ? 'zinnia'
    : s.kind === 'group' ? `group::${s.projectId}`
      : `agent::${s.projectId}::${s.agentId}`;

const isRowSelected = (sel: Selection, want: Selection): boolean => selKey(sel) === selKey(want);

/*
 * [v0.39.1 #1] 选中态 = `.navrow.selected`（CSS 里新增的整行底色，比 hover 深一档）。
 *   SELECTED_BAR（inset 左边线）已删；也不再借用 .navrow.active——
 *   active 在收藏/设置里另有含义（底色 = hover 同色 + 图标染色），语义不同不混用。
 */

// ═══════════════════════════════════════════════════════════════
// 主组件
// ═══════════════════════════════════════════════════════════════

export interface ContactsSearchFocus {
  projectId: string | null;
  agentId: string | null;
  requestId: number;
}

export interface ContactsViewProps {
  searchFocus?: ContactsSearchFocus | null;
  onSearchFocusDone?: (requestId: number) => void;
}

export const ContactsView: React.FC<ContactsViewProps> = ({
  searchFocus = null, onSearchFocusDone,
}) => {
  const { t } = useTranslation();
  /*
   * [#4] 左栏订阅整棵 convs：immer 之下每次事件都会给出新引用 → 分组、宫格、
   *   成员计数、下钻列表全部随事件实时刷新。列表本身很轻（几十行 DOM），
   *   不为它做更细的选择器切分——被藏起的 ChatStream 同样在整棵树上重渲。
   */
  const convs = useKnoweStore((s) => s.convs);

  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Selection>({ kind: 'zinnia' });
  // 分组 / 下钻的手动展开态；搜索时对「有命中」的分组强制展开（见下）。
  const [sectionOpen, setSectionOpen] = useState<{ zinnia: boolean; groups: boolean }>({ zinnia: true, groups: true });
  const [drillOpen, setDrillOpen] = useState<Record<string, boolean>>({});

  // 全局搜索命中联系人后：清掉本页筛选，展开所属分组并选中现有资料页。
  useLayoutEffect(() => {
    if (!searchFocus) return;
    const { projectId, agentId } = searchFocus;
    setQuery('');
    if (!projectId || !agentId) {
      setSectionOpen((current) => ({ ...current, zinnia: true }));
      setSelected({ kind: 'zinnia' });
    } else {
      setSectionOpen((current) => ({ ...current, groups: true }));
      setDrillOpen((current) => ({ ...current, [projectId]: true }));
      setSelected({ kind: 'agent', projectId, agentId });
    }
    onSearchFocusDone?.(searchFocus.requestId);
  }, [onSearchFocusDone, searchFocus]);

  const q = query.trim().toLowerCase();
  const hit = useCallback((...fields: (string | undefined)[]) => {
    if (!q) return true;
    return fields.some((f) => (f || '').toLowerCase().includes(q));
  }, [q]);

  // ── 数据整形：知知 + 项目群聊（平台会话和 DM 都不进入项目卡片区）──
  const groups = useMemo(() => {
    return Object.values(convs)
      .filter((c): c is Conv => !!c
        && c.projectId !== PLATFORM_PROJECT_ID
        && !isPrivateChat(c.projectId))
      .map((c) => ({ conv: c, members: liveMembers(c) }))
      .sort((a, b) =>
        (a.conv.projectName || a.conv.projectId).localeCompare(b.conv.projectName || b.conv.projectId, 'zh'));
  }, [convs]);

  // 搜索过滤：分组名命中 → 展示全部成员；否则只展示命中的成员；两者皆无则整组隐藏。
  const shownGroups = useMemo(() => {
    if (!q) return groups.map((g) => ({ ...g, forceOpen: false }));
    const out: { conv: Conv; members: Member[]; forceOpen: boolean }[] = [];
    for (const g of groups) {
      const nameHit = hit(g.conv.projectName, g.conv.projectId);
      const memberHits = g.members.filter((m) => hit(m.display.name, m.display.role, roleLabel(m.display.role)));
      if (nameHit) out.push({ conv: g.conv, members: g.members, forceOpen: memberHits.length > 0 });
      else if (memberHits.length > 0) out.push({ conv: g.conv, members: memberHits, forceOpen: true });
    }
    return out;
  }, [groups, q, hit]);

  const zinniaShown = hit(getZinniaDisplayName(), '接待', 'zinnia', '知知', '系统');

  // ── 进聊天：一律走既有 action，切走前 setView('chats')；记录抽屉按意图开/关 ──
  const enterChat = useCallback((projectId: string, withRecords: boolean) => {
    const st = useKnoweStore.getState();
    st.switchProject(projectId);
    const rec = useRecordsStore.getState();
    if (withRecords) rec.openDrawer(); else rec.closeDrawer();
    st.setView('chats');
  }, []);
  const enterDmChat = useCallback((projectId: string, agentId: string, withRecords: boolean) => {
    const st = useKnoweStore.getState();
    st.enterDm(projectId, agentId);
    const rec = useRecordsStore.getState();
    if (withRecords) rec.openDrawer(); else rec.closeDrawer();
    st.setView('chats');
  }, []);

  // 删除成功后才收敛联系人视图；失败时后端保持原状，卡片也原样留在列表里。
  const onProjectDeleted = useCallback((projectId: string) => {
    setSelected((current) => {
      if (current.kind === 'group' && current.projectId === projectId) return { kind: 'empty' };
      if (current.kind === 'agent' && current.projectId === projectId) return { kind: 'empty' };
      return current;
    });
    setDrillOpen((current) => {
      if (!Object.prototype.hasOwnProperty.call(current, projectId)) return current;
      const next = { ...current };
      delete next[projectId];
      return next;
    });
  }, []);

  const onAgentDeleted = useCallback((projectId: string, agentId: string) => {
    setSelected((current) => (
      current.kind === 'agent'
      && current.projectId === projectId
      && current.agentId === agentId
        ? { kind: 'empty' }
        : current
    ));
  }, []);

  const anyShown = zinniaShown || shownGroups.length > 0;

  const api: PanelApi = { enterChat, enterDmChat, onSelect: setSelected };

  return (
    <>
      {/* ═══ 左栏：联系人列表 ═══ */}
      <aside className="side">
        <div className="side-head"><div className="side-title">{t('contacts.view.29')}</div></div>

        <div className="search-wrap">
          <div className="search">
            <IcSearch />
            <input
              placeholder={t('contacts.view.15')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label={t('contacts.view.15')}
            />
          </div>
        </div>

        <div className="side-scroll">
          {/* ① 知知 & 系统 */}
          {zinniaShown && (
            <>
              <div
                className={'sec-head' + (sectionOpen.zinnia ? '' : ' collapsed')}
                onClick={() => setSectionOpen((s) => ({ ...s, zinnia: !s.zinnia }))}
              >
                <span className="chev"><IcChevDown /></span>
                {t('common.10')} &amp; {t('contacts.view.28')}
                <span className="cnt">1</span>
              </div>
              <Collapse open={sectionOpen.zinnia} className="sec-body">
                <ContactRow
                  face={zinniaFace(t)}
                  size={32}
                  selected={isRowSelected(selected, { kind: 'zinnia' })}
                  onClick={() => setSelected({ kind: 'zinnia' })}
                  onDoubleClick={() => enterChat(PLATFORM_PROJECT_ID, false)}
                />
              </Collapse>
            </>
          )}

          {/* ② 项目群聊 */}
          <div
            className={'sec-head' + (sectionOpen.groups ? '' : ' collapsed')}
            onClick={() => setSectionOpen((s) => ({ ...s, groups: !s.groups }))}
          >
            <span className="chev"><IcChevDown /></span>
            {t('contacts.view.groupChat')}
            <span className="cnt">{shownGroups.length}</span>
          </div>
          <Collapse open={sectionOpen.groups} className="sec-body">
            {shownGroups.length === 0 ? (
              <div className="navrow" style={{ cursor: 'default', color: 'var(--ink-3)', fontSize: 13 }}>
                <span className="navrow-nm" style={{ color: 'var(--ink-3)' }}>
                  {q ? t('contacts.view.80') : t('contacts.view.90')}
                </span>
              </div>
            ) : shownGroups.map(({ conv, members, forceOpen }) => {
              const open = forceOpen || !!drillOpen[conv.projectId];
              const groupSel = isRowSelected(selected, { kind: 'group', projectId: conv.projectId });
              return (
                <React.Fragment key={conv.projectId}>
                  {/* 群行：单击行身展开/收起成员（v1.0.23.6 取代原 chev 交互）；双击进群聊 */}
                  <div
                    className={'navrow grp-row' + (open ? ' open' : '') + (groupSel ? ' selected' : '')}
                    onClick={() => {
                      setSelected({ kind: 'group', projectId: conv.projectId });
                      setDrillOpen((d) => ({ ...d, [conv.projectId]: !open }));
                    }}
                    onDoubleClick={() => enterChat(conv.projectId, false)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      openContactGroupMenu(
                        conv.projectId, e.clientX, e.clientY,
                        () => onProjectDeleted(conv.projectId),
                      );
                    }}
                    title={conv.projectName || conv.projectId}
                  >
                    {/*
                      [v0.39.1 #3] 群头像 = 聊天列表同一套宫格（AvatarGrid，44px 自适应密度）。
                      喂料按它的既定约定：归档滤掉、项目经理第一（gridMembersOf）。
                      成员增删 → conv.members 变 → 本组件重渲 → 宫格立刻更新。
                    */}
                    <AvatarGrid
                      members={gridMembersOf(conv)}
                      title={conv.projectName || conv.projectId}
                    />
                    <span className="navrow-nm">{conv.projectName || conv.projectId}</span>
                    <span className="navrow-cnt">{t('common.peopleCount', { n: members.length })}</span>
                  </div>

                  {/* 下钻：该群的成员（Agent 的唯一入口）。[#4] 直接由订阅数据映射，增删即刻反映。 */}
                  <Collapse open={open} className="drill">
                    {members.map((m) => {
                      const f = memberFace(conv, m);
                      const memSel = isRowSelected(selected, { kind: 'agent', projectId: conv.projectId, agentId: m.id });
                      return (
                        <ContactRow
                          key={m.id}
                          face={f}
                          size={28}
                          selected={memSel}
                          onClick={() => setSelected({ kind: 'agent', projectId: conv.projectId, agentId: m.id })}
                          onDoubleClick={() => enterDmChat(conv.projectId, m.id, false)}
                          onContextMenu={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            openContactAgentMenu(
                              conv.projectId, m.id, e.clientX, e.clientY,
                              () => onAgentDeleted(conv.projectId, m.id),
                            );
                          }}
                        />
                      );
                    })}
                  </Collapse>
                </React.Fragment>
              );
            })}
          </Collapse>

          {!anyShown && (
            <div className="navrow" style={{ cursor: 'default' }}>
              <span className="navrow-nm" style={{ color: 'var(--ink-3)' }}>{t('contacts.view.72')}</span>
            </div>
          )}
        </div>
      </aside>

      {/* ═══ 右栏：资料页（[#4] 各面板按 id 自行订阅 store，实时联动）═══ */}
      <div className="stage">
        <div className="stage-card">{renderPanel(selected, api)}</div>
      </div>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════
// 列表行（知知 / 群内成员通用）
// ═══════════════════════════════════════════════════════════════

const ContactRow: React.FC<{
  face: Face;
  size: 28 | 32;
  selected: boolean;
  onClick: () => void;
  onDoubleClick: () => void;
  onContextMenu?: React.MouseEventHandler<HTMLDivElement>;
}> = ({ face, size, selected, onClick, onDoubleClick, onContextMenu }) => (
  <div
    className={'navrow' + (selected ? ' selected' : '')}
    onClick={onClick}
    onDoubleClick={onDoubleClick}
    onContextMenu={onContextMenu}
    title={face.name}
  >
    <Avatar glyph={face.glyph} pal={face.pal} size={size} src={face.src} />
    <span className="navrow-nm">{face.name}</span>
    <span className="navrow-tag">{roleLabel(face.role)}</span>
  </div>
);

// ═══════════════════════════════════════════════════════════════
// 右侧资料页
// ═══════════════════════════════════════════════════════════════

interface PanelApi {
  enterChat: (projectId: string, withRecords: boolean) => void;
  enterDmChat: (projectId: string, agentId: string, withRecords: boolean) => void;
  onSelect: (s: Selection) => void;
}

/** [#4] 面板只收 id，不收对象快照——数据由面板自己向 store 订阅，事件到即更新。 */
function renderPanel(sel: Selection, api: PanelApi): React.ReactElement {
  if (sel.kind === 'empty') return <EmptyPanel />;
  if (sel.kind === 'zinnia') return <ZinniaProfile api={api} />;
  if (sel.kind === 'group') return <GroupPanel projectId={sel.projectId} api={api} />;
  return <AgentProfile projectId={sel.projectId} agentId={sel.agentId} api={api} />;
}

const EmptyPanel: React.FC = () => {
  const { t } = useTranslation();
  return (
    <div className="empty2">
      <div className="e-ic"><IcUsers size={52} /></div>
      <p>{t('contacts.view.92')}</p>
    </div>
  );
};

/**
 * [v0.39.1 #2] 资料页头部头像的唯一形态：<Avatar size={44}>（合法尺寸类 av-44），
 *   由 CSS `.pf-top > .avatar` 统一放大到 64px。知知 / 项目经理 / Worker 全走这一条，
 *   尺寸由**容器**说了算，再也轮不到图片的固有分辨率插嘴。
 */

// ── 知知资料页（平台级、无运行参数）──
const ZinniaProfile: React.FC<{ api: PanelApi }> = ({ api }) => {
  const { t } = useTranslation();
  return (
    <div className="profile">
      <div className="pf-top">
        <Avatar glyph={ZINNIA_FACE_STATIC.glyph} pal={ZINNIA_FACE_STATIC.pal} size={44} src={ZINNIA_FACE_STATIC.src} />
        <div>
          <div className="pf-nm">{getZinniaDisplayName()}</div>
          <div className="pf-role">{t('contacts.view.56')}</div>
        </div>
      </div>

      <div className="pf-sec">
        <h4>{t('contacts.view.49')}</h4>
        <Kv k={t('contacts.view.52')} v={getZinniaDisplayName()} />
        <Kv k="Agent ID" v={t('contacts.view.35')} />
        <Kv k={t('contacts.view.27')} v={t('contacts.view.55')} />
        {/* [v0.39.1 #5] 知知也有协作权限栏 */}
        <Kv k={t('contacts.view.41')} v={permissionZinnia()} />
      </div>

      <div className="pf-sec">
        <h4>{t('contacts.view.78')}</h4>
        <Kv k={t('contacts.view.85')} v={t('contacts.view.64')} />
        {/* [v0.39.1 #6] 一句完整的自然语言描述 */}
        <Kv k={t('contacts.view.71')} v={skillZinnia()} />
      </div>

      <div className="pf-acts">
        <Act icon={<IcMsg />} label={t('contacts.view.42')} onClick={() => api.enterChat(PLATFORM_PROJECT_ID, false)} />
        <Act icon={<IcReport />} label={t('contacts.view.77')} onClick={() => api.enterChat(PLATFORM_PROJECT_ID, true)} />
      </div>
    </div>
  );
};

// ── 群内 Agent 资料页（项目经理 / Worker 同一条渲染路径，#2）──
const AgentProfile: React.FC<{ projectId: string; agentId: string; api: PanelApi }> = ({
  projectId, agentId, api,
}) => {
  const { t } = useTranslation();
  /*
   * [#4] 只订「本项目」这一棵：immer 之下别的项目变化不会给这个引用换新，
   *   本面板就不空转；本项目的任何变化（成员改名、换状态、归档）立刻到达。
   */
  const conv = useKnoweStore((s) => s.convs[projectId]);

  /*
   * [v0.44 §3.2] 模型独立设置：订阅设置 store 里「自己这一条」绑定。
   *   没配过 → null → 模块进入「跟随全局主模型」展示态。保存/修改/清除都是
   *   settings store 的 action（推后端、全局覆盖清空等规矩都在 store 里，
   *   这里只管摆界面）。hook 全部在早退之前调用，规则安全。
   */
  const agentBinding = useSettingsStore(
    (s) => s.agentModels[agentBindingKey(projectId, agentId)] ?? null,
  );
  const globalMain = useSettingsStore((s) => s.mainModel);
  const saveAgentModel = useSettingsStore((s) => s.saveAgentModel);
  const editAgentModel = useSettingsStore((s) => s.editAgentModel);
  const clearAgentModel = useSettingsStore((s) => s.clearAgentModel);

  if (!conv) return <EmptyPanel />;
  const member = (conv.members || []).find((m) => m.id === agentId);
  if (!member) return <EmptyPanel />;

  const f = memberFace(conv, member);
  const proj = conv.projectName || conv.projectId;

  return (
    <div className="profile">
      {/* [#2] 头部：与知知 / 群资料页同一结构，Avatar 走合法的 44 号类，CSS 统一放大到 64。 */}
      <div className="pf-top">
        <Avatar glyph={f.glyph} pal={f.pal} size={44} src={f.src} />
        <div>
          <div className="pf-nm">{f.name}</div>
          <div className="pf-role">{subtitleOf(conv, member)}</div>
        </div>
      </div>

      <div className="pf-sec">
        <h4>{t('contacts.view.49')}</h4>
        <Kv k={t('contacts.view.52')} v={f.name} />
        <Kv k="Agent ID" v={member.id} />
        <Kv k={t('contacts.view.93')} v={proj} />
        {/* [#4] 空闲 ↔ 工作中随事件实时切换；归档态也如实标出 */}
        <Kv k={t('contacts.view.58')} v={stateLabelOf(member)} />
        {/* [#5] 「仅协作」→ 按角色模板的沙箱/记忆/工具综合说明 */}
        <Kv k={t('contacts.view.41')} v={permissionOf(member)} />
      </div>

      <div className="pf-sec">
        <h4>{t('contacts.view.34')}</h4>
        <Kv k={t('contacts.view.85')} v={agentSoul(conv, member)} />
        {/* [#6] 一句完整的自然语言描述（24 角色逐一成文 + 兜底模板） */}
        <Kv k={t('contacts.view.71')} v={skillOf(member)} />
      </div>

      {/*
        [v0.44 §3.1] 「运行参数」区退役：温度、最大迭代两项随本版本删除（README §3.1，
        与设置页去掉温度同一批口径）。
        [v0.44 §3.2] 取而代之：per-Agent 模型独立设置——厂商/模型/Key 的交互与
        「设置 → 模型与提供方」完全一致（同一个 ModelBindingModule）。
        没个性化时展示「跟随全局主模型」；在设置页更新全局主模型会**覆盖**这里的
        个性化（settings store 的 saveMainModel 清空 agentModels，后端同规则兜底）。
      */}
      <div className="pf-sec">
        <h4>{t('common.18')}</h4>
        <div className="pf-scope">
          {t('contacts.view.modelOverrideNote', { proj, role: roleLabel(member.display.role) || t('common.07') })}
        </div>
        <ModelBindingModule
          binding={agentBinding}
          followBinding={globalMain && globalMain.sealed ? globalMain : null}
          followNote={t('contacts.view.59')}
          onSave={(b) => {
            saveAgentModel(projectId, agentId, b);
            toast(t('contacts.view.modelSaved'));
          }}
          onEdit={() => editAgentModel(projectId, agentId)}
          extraAction={agentBinding ? (
            <button
              type="button"
              className="test-btn"
              onClick={() => {
                clearAgentModel(projectId, agentId);
                toast(t('contacts.view.followGlobalSaved'));
              }}
            >
              {t('contacts.view.followGlobal')}
            </button>
          ) : undefined}
        />
      </div>

      <div className="pf-acts">
        <Act icon={<IcMsg />} label={t('contacts.view.42')} onClick={() => api.enterDmChat(projectId, agentId, false)} />
        <Act icon={<IcReport />} label={t('contacts.view.77')} onClick={() => api.enterDmChat(projectId, agentId, true)} />
      </div>
    </div>
  );
};

// ── 群聊资料页（单击群行时的右侧面板：概览 + 成员 + 快捷进入）──
const GroupPanel: React.FC<{ projectId: string; api: PanelApi }> = ({ projectId, api }) => {
  const { t } = useTranslation();
  // [#4] 同 AgentProfile：按 id 订阅本项目，成员数 / 成员列表 / 宫格随事件即时刷新。
  const conv = useKnoweStore((s) => s.convs[projectId]);

  /*
   * [v0.44 §3.3] 本群审批卡超时：生效值 = 群级覆盖 > 全局（effectiveGroupTimeout）。
   *   这里改的**只是本群**；在「设置 → 通知」改全局值时，settings store 会清空所有
   *   群级覆盖（全局覆盖个性，后端 runtime_settings 同规则兜底）。
   *   hook 在早退之前调用，规则安全。
   */
  const groupTimeout = useSettingsStore((s) => effectiveGroupTimeout(s, projectId));
  const setGroupApprovalTimeout = useSettingsStore((s) => s.setGroupApprovalTimeout);

  if (!conv) return <EmptyPanel />;

  const proj = conv.projectName || conv.projectId;
  const members = liveMembers(conv);
  const grid = gridMembersOf(conv);

  return (
    <div className="profile">
      <div className="pf-top">
        {/* [#3] 头部也用聊天列表同一套宫格；44px 原生盒等比放大到 64，与其它资料页头部同尺寸。 */}
        <div style={{ width: 64, height: 64, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ transform: `scale(${64 / 44})` }}>
            <AvatarGrid members={grid} title={proj} />
          </div>
        </div>
        <div>
          <div className="pf-nm">{proj}</div>
          <div className="pf-role">{t('contacts.view.groupMembers', { n: members.length })}</div>
        </div>
      </div>

      <div className="pf-sec">
        <h4>{t('contacts.view.49')}</h4>
        <Kv k={t('contacts.view.95')} v={conv.projectId} />
        <Kv k={t('contacts.view.60')} v={t('common.peopleCount', { n: members.length })} />
        {/*
          [v0.39.3] 项目目录：取代原「工作目录」纯文本行（同一 conv.projectDir，避免重复展示）。
          · 复用 v0.39.2 修好的 .pf-kv：标题 .k 定宽 80px 不收缩，路径 .v flex:1 自适应换行——
            长路径折成两三行也不会挤压左侧「项目目录」四个字；行高随之向下伸展。
          · 「项目目录」后紧跟文件夹图标（FolderButton），点击→系统文件管理器打开项目根目录。
          「项目目录」(约 56px) + 图标(14px) 在 80px 标题列里放得下；图标 vertical-align:middle
          贴在标题首行，路径换行时标题仍留在顶部第一行。
        */}
        <div className="pf-kv">
          <span className="k">{t('common.04')}<FolderButton dir={conv.projectDir} /></span>
          <span className="v">{conv.projectDir || t('contacts.view.74')}</span>
        </div>
      </div>

      {/* [v0.44 §3.3] 群级审批卡超时（只改本群；全局改动会覆盖这里）。 */}
      <div className="pf-sec">
        <h4>{t('common.12')}</h4>
        <div className="pf-setrow">
          <div className="pf-setlabel">
            <div className="si-t">{t('contacts.view.09')}</div>
            <div className="si-d">{t('contacts.view.43')}</div>
          </div>
          <MSetSelect
            value={groupTimeout}
            options={APPROVAL_TIMEOUT_OPTIONS.map((o) => ({ value: o.value, label: t(o.label) }))}
            onChange={(v) => {
              setGroupApprovalTimeout(projectId, v);
              toast(t('contacts.view.approvalTimeoutUpdated'));
            }}
            ariaLabel={t('contacts.view.75')}
          />
        </div>
      </div>

      <div className="pf-sec">
        <h4>{t('common.07')}</h4>
        {members.length === 0 ? (
          <div className="pf-kv"><span className="k">{t('contacts.view.73')}</span><span className="v" /></div>
        ) : members.map((m) => {
          const f = memberFace(conv, m);
          return (
            <div
              key={m.id}
              onClick={() => api.onSelect({ kind: 'agent', projectId: conv.projectId, agentId: m.id })}
              onDoubleClick={() => api.enterDmChat(conv.projectId, m.id, false)}
              style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: '8px 0', cursor: 'pointer' }}
              title={t('contacts.view.viewProfile', { name: f.name })}
            >
              <Avatar glyph={f.glyph} pal={f.pal} size={28} src={f.src} />
              <span style={{ flex: 1, fontSize: 14, color: 'var(--ink)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
              <span style={{ fontSize: 12, color: 'var(--ink-3)', flexShrink: 0 }}>{f.role}</span>
            </div>
          );
        })}
      </div>

      <div className="pf-acts">
        <Act icon={<IcMsg />} label={t('contacts.view.91')} onClick={() => api.enterChat(conv.projectId, false)} />
        <Act icon={<IcReport />} label={t('contacts.view.77')} onClick={() => api.enterChat(conv.projectId, true)} />
      </div>
    </div>
  );
};

// ── 资料页小构件 ──
const Kv: React.FC<{ k: string; v: string }> = ({ k, v }) => (
  <div className="pf-kv"><span className="k">{k}</span><span className="v">{v}</span></div>
);

const Act: React.FC<{ icon: React.ReactNode; label: string; onClick: () => void }> = ({ icon, label, onClick }) => (
  <div className="pf-act" onClick={onClick} role="button" tabIndex={0} aria-label={label}>
    <div className="pa-ic">{icon}</div>
    <span>{label}</span>
  </div>
);

export default ContactsView;
