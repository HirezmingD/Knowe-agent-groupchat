/**
 * KnowledgeView.tsx — [v0.43] 知识库视图（知识系统重构 · 设计报告 §四/§六 + 新增需求 1–3）
 *
 * 与 ContactsView / FavoritesView 同款 SidePanel + Stage 骨架（activeView==='knowledge'，
 * App.tsx 零改动）。v0.41 的「按类型 / 按范围」平铺筛选，这版换成**两大标签的树**：
 *
 *   【知识图谱】(L1，默认展开)
 *      ├ 全局知识  (L2，可选可折叠)          ← scope==='global'
 *      │   ├ 约定 / 坑 / 模式 / 清单 (L3)    ← cat 三级筛选
 *      └ 项目知识  (L2)                      ← scope==='project'
 *          └ 约定 / 坑 / 模式 / 清单 (L3)
 *   【技能包】(L1)
 *      ├ 系统技能包 (L2)
 *      │   ├ 系统自备技能 (L3，永久生效、不可变)
 *      │   └ 项目经验技能 (L3，核心知识导出、独立策展)
 *      └ 第三方技能包 (L2，真实安装目录 + 独立生命周期；安装入口先留口)
 *
 * 数据（禁止 mock）：store/knowledge.ts → knowledge_api.py（本机 HTTP 数据面；
 * WS 契约照旧不碰）。卡片=知识资产（五类蒸馏资产，不再是 handoff 切片）；
 * 挂载即拉取 + 45s 静默轮询；筛选全在前端（数据量不大）。
 *
 * 右键菜单（新增需求 2，项与顺序固定）：
 *   ① 重命名        —— 只改标题，正文一字不动（弹窗里也这么写明）；
 *   ② 调整范围      —— app 内**雾化弹窗**（.kn-scrim-blur）改二级(全局/项目)+三级(四类)，
 *                      一次 POST，harness 侧注入/画像/技能导出**实时**跟着变——
 *                      这两级就是「价值判断差异」落到 agent 复用机制的旋钮；
 *   ③ 预览          —— 右侧滑入面板（KnowledgePreview，同文件预览那套被认可的交互），
 *                      内容 = ASSET.md 的美观 markdown（harness 制卡时已保证排版）；
 *   ④ 查看引用历史  —— 使用轨迹（真实 usage 事件）+ 沉淀来源（证据清单）；
 *   ─────
 *   ⑤ 退役(停用不删除) / 恢复启用；
 *   ⑥ 彻底删除      —— ⑤ 的正下方（新增需求 2④），强确认，POST /purge 不可逆。
 *
 * 技能包行有**独立**右键菜单：系统自备技能完全禁用右键；项目经验技能按
 * 待审/生效/退役三态策展；第三方技能保留自己的状态与卸载扩展。知识卡菜单不复用。
 *
 * 待审策展（报告 §5）：candidate/冲突/退役建议 都落在「待审」段；卡片内联
 * [批准][驳回] —— approve→validated 进注入池，reject→retired（保留可恢复）。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useKnoweStore } from '../store/store';
import { useRecordsStore } from '../store/records';
import { memberNameLabel } from '../shared/roleLabel';
import { confirmModal, openMenu, toast, type MenuEntry } from './ContextMenu';
import {
  useKnowledgeStore, knowDateLabel, sourceLabel, USAGE_ZH,
  type KnowCard, type KnowCat, type KnowScope, type KnowSt, type KnowAssetDetail,
  type SkillPack, type SkillPackStatus,
} from '../store/knowledge';
import {
  IconBook, IconAlert, IconSpark, IconCheckbox, IconFolder,
  IconEdit, IconReport, IconRecover, IconTrash, IconChevR,
} from './icons';
import { KnowledgePreview } from './KnowledgePreview';
import './knowledge-view.css';

// ═══════════════════════════════════════════════════════════════
// 图标（icons.tsx 缺的就地画——沿用 FavoritesView「尚无则内联」先例，不动共享清单）
// ═══════════════════════════════════════════════════════════════

/** reference ICON.scope（16px 地球）——「全局知识」/「调整范围」 */
const IcScope: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="9" />
    <path d="M2.5 12h19M12 2.5a15 15 0 0 1 0 19M12 2.5a15 15 0 0 0 0 19" />
  </svg>
);

/** reference ICON.retire（16px 圈内勾）——「退役(停用不删除)」 */
const IcRetire: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" />
  </svg>
);

/** 眼睛（16px）——「预览」 */
const IcEye: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

/** 拼图（16px）——技能包 */
const IcPuzzle: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10 3.5a1.8 1.8 0 0 1 3.6 0V5H17a2 2 0 0 1 2 2v3.2h-1.5a1.8 1.8 0 0 0 0 3.6H19V17a2 2 0 0 1-2 2h-3.2v-1.5a1.8 1.8 0 0 0-3.6 0V19H7a2 2 0 0 1-2-2v-3.2h1.5a1.8 1.8 0 0 0 0-3.6H5V7a2 2 0 0 1 2-2h3z" />
  </svg>
);

/** 插头（16px）——技能插座 MCP */
const IcPlug: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22v-5" />
    <path d="M9 8V2" />
    <path d="M15 8V2" />
    <path d="M18 8v3a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z" />
  </svg>
);

/** 插头大图标（52px）——技能插座空态（同 BookBig 先例） */
const PlugBig: React.FC = () => (
  <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22v-5" />
    <path d="M9 8V2" />
    <path d="M15 8V2" />
    <path d="M18 8v3a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z" />
  </svg>
);

/** 空态大图标：emptyBox 会把图标放大到 52，照做 */
const BookBig: React.FC = () => (
  <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H12v15H5.5A1.5 1.5 0 0 1 4 17.5z" />
    <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H12v15h6.5a1.5 1.5 0 0 0 1.5-1.5z" />
  </svg>
);

// ═══════════════════════════════════════════════════════════════
// 分类表 / 段表
// ═══════════════════════════════════════════════════════════════

const CATS: { id: KnowCat; icon: React.ReactNode }[] = [
  { id: '约定', icon: <IconBook /> },
  { id: '坑', icon: <IconAlert /> },
  { id: '模式', icon: <IconSpark /> },
  { id: '清单', icon: <IconCheckbox /> },
];

const SCOPES: { id: KnowScope; label: string; icon: React.ReactNode }[] = [
  { id: 'global', label: 'knowledge.preview.03', icon: <IcScope /> },
  { id: 'project', label: 'knowledge.preview.13', icon: <IconFolder /> },
];

const SEGS: { label: string; st: KnowSt | null }[] = [
  { label: 'common.15', st: null },
  { label: 'knowledge.preview.10', st: 'ok' },
  { label: 'knowledge.preview.05', st: 'pending' },
  { label: 'knowledge.preview.04', st: 'retired' },
];

const REFRESH_MS = 45_000;   // 背景蒸馏是异步的，轮询兜底让新知识自己长出来

type SkillPane = 'root' | 'system' | 'builtin' | 'experience' | 'third' | 'mcpRoot' | 'mcpSystem' | 'mcpThird';

// ═══════════════════════════════════════════════════════════════
// 弹窗状态（本视图私有）
// ═══════════════════════════════════════════════════════════════

type ModalState =
  | { kind: 'rename'; card: KnowCard }
  | { kind: 'scope'; card: KnowCard }
  | { kind: 'retire'; card: KnowCard }
  | { kind: 'purge'; card: KnowCard }
  | { kind: 'history'; card: KnowCard }
  | null;

// ═══════════════════════════════════════════════════════════════

export const KnowledgeView: React.FC = () => {
  const { t } = useTranslation();
  const cards = useKnowledgeStore((s) => s.cards);
  const loading = useKnowledgeStore((s) => s.loading);
  const loadedAt = useKnowledgeStore((s) => s.loadedAt);
  const error = useKnowledgeStore((s) => s.error);
  const skillpacks = useKnowledgeStore((s) => s.skillpacks);
  const profileProjects = useKnowledgeStore((s) => s.profileProjects);
  // 订阅 convs：来源行「项目名 · Agent 名」要跟着花名册/项目事件实时对。
  const convs = useKnoweStore((s) => s.convs);

  const [q, setQ] = useState('');
  const [seg, setSeg] = useState<KnowSt | null>(null);
  const [modal, setModal] = useState<ModalState>(null);

  // ── 侧栏树导航状态（新增需求 1）──
  const [pane, setPane] = useState<'graph' | 'skills'>('graph');
  const [expandGraph, setExpandGraph] = useState(true);       // L1 知识图谱默认展开
  const [expandSkills, setExpandSkills] = useState(true);   // L1 技能包默认展开（v1.0.23.6 只到 L2）
  const [expandSystemSkills, setExpandSystemSkills] = useState(false); // L3 默认折叠，点 L2 行尾箭头展开
  const [expandMcp, setExpandMcp] = useState(true);         // L1 技能插座默认展开到 L2（v1.0.23.6）
  const [expandScope, setExpandScope] = useState<Record<KnowScope, boolean>>({
    // [v1.0.23.6] 默认只展开到 L2（全局/项目），L3 分类折叠——每次进入知识图谱不再默认铺开三级
    global: false, project: false,
  });
  const [scope, setScope] = useState<KnowScope | null>(null); // L2 选中
  const [cat, setCat] = useState<KnowCat | null>(null);       // L3 选中
  const [skillPane, setSkillPane] = useState<SkillPane>('root');

  // 挂载即拉取；45s 静默刷新；卸载即停。
  useEffect(() => {
    void useKnowledgeStore.getState().load();
    const timer = window.setInterval(
      () => { void useKnowledgeStore.getState().load({ silent: true }); },
      REFRESH_MS,
    );
    return () => window.clearInterval(timer);
  }, []);

  // 进【技能包】才拉真实技能数据；保持在该面板时轮询，承接后台 core→待审技能的异步同步。
  useEffect(() => {
    if (pane !== 'skills') return undefined;
    void useKnowledgeStore.getState().loadSkillpacks();
    const timer = window.setInterval(
      () => { void useKnowledgeStore.getState().loadSkillpacks(); },
      REFRESH_MS,
    );
    return () => window.clearInterval(timer);
  }, [pane]);

  const kw = q.trim();
  const shown = useMemo(() => {
    let list = cards;
    if (seg) list = list.filter((c) => c.st === seg);
    if (scope) list = list.filter((c) => c.scope === scope);
    if (cat) list = list.filter((c) => c.cat === cat);
    if (kw) list = list.filter((c) => c.title.includes(kw) || c.body.includes(kw));
    return list;
  }, [cards, seg, scope, cat, kw]);

  const packsForPane = useMemo(() => {
    let list: SkillPack[];
    if (skillPane === 'builtin') list = skillpacks.systemBuiltin;
    else if (skillPane === 'experience') list = skillpacks.projectExperience;
    else if (skillPane === 'third') list = skillpacks.thirdParty;
    else if (skillPane === 'system') {
      list = [...skillpacks.systemBuiltin, ...skillpacks.projectExperience];
    } else {
      list = [
        ...skillpacks.systemBuiltin,
        ...skillpacks.projectExperience,
        ...skillpacks.thirdParty,
      ];
    }
    return list;
  }, [skillpacks, skillPane]);

  const shownPacks = useMemo(() => {
    let list = packsForPane;
    if (seg) list = list.filter((p) => p.st === seg);
    if (kw) list = list.filter((p) => (
      p.name.includes(kw) || p.description.includes(kw) || p.packId.includes(kw)
    ));
    return list;
  }, [packsForPane, seg, kw]);

  const pending = useMemo(() => cards.filter((c) => c.st === 'pending').length, [cards]);
  const pendingSkills = useMemo(
    () => packsForPane.filter((p) => p.st === 'pending').length,
    [packsForPane],
  );
  const monthNew = useMemo(() => {
    const now = new Date();
    return cards.filter((c) => {
      if (!c.firstSeen) return false;
      const d = new Date(c.firstSeen);
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
    }).length;
  }, [cards]);

  // ── 树点击语义 ──
  const clickGraphL1 = (): void => {
    if (pane !== 'graph') { setPane('graph'); setExpandGraph(true); return; }
    setExpandGraph((v) => !v);
  };
  const clickSkillsL1 = (): void => {
    // [v1.0.24.1] 与知识图谱/技能插座统一：切过来=切换+展开（不 toggle）；当前视图内再点=toggle
    const wasSkills = pane === 'skills' && skillPane === 'root';
    setSkillPane('root');
    if (pane !== 'skills' || !wasSkills) { setPane('skills'); setExpandSkills(true); return; }
    setExpandSkills((v) => !v);
  };
  const clickMcpL1 = (): void => {
    // [v1.0.24.1] 与技能包/知识图谱统一：切过来=切换+展开（不 toggle）；当前视图内再点=toggle
    const wasMcp = pane === 'skills' && skillPane === 'mcpRoot';
    setSkillPane('mcpRoot');
    if (pane !== 'skills' || !wasMcp) { setPane('skills'); setExpandMcp(true); return; }
    setExpandMcp((v) => !v);
  };
  const clickScopeL2 = (id: KnowScope): void => {
    setPane('graph');
    if (scope === id) { setScope(null); setCat(null); return; }  // 再点取消选中
    setScope(id);
    setCat(null);                                                // 换范围清三级
  };
  const clickCatL3 = (sc: KnowScope, id: KnowCat): void => {
    setPane('graph');
    if (scope === sc && cat === id) { setCat(null); return; }  // 同类再点→只留范围
    setScope(sc);
    setCat(id);
  };

  // ── 来源行数据：项目名 / Agent 名从主 store 的会话与花名册里解析 ──
  const projName = (pid: string): string => convs[pid]?.projectName || pid;
  const agentName = (pid: string, aid: string | null): string => {
    if (!aid) return t('common.07');
    const m = convs[pid]?.members?.find((x) => x.id === aid);
    if (m) return memberNameLabel(m.id, m.display.name);
    return aid === 'coordinator' ? t('common.06') : aid;
  };

  // ── 来源跳转：项目聊天 + 记录抽屉（报告都在抽屉里）──
  const jumpToSource = (card: KnowCard): void => {
    const st = useKnoweStore.getState();
    if (!st.convs[card.projectId]) {
      toast(t('knowledge.view.noSourceConv'), 'warn');
      return;
    }
    st.switchProject(card.projectId);
    useRecordsStore.getState().openDrawer();
    st.setView('chats');
  };

  // ── 待审策展：批准 / 驳回（approve→validated；reject→retired 可恢复）──
  const doReview = (card: KnowCard, action: 'approve' | 'reject'): void => {
    void useKnowledgeStore.getState().review(card, action).then((ok) => {
      if (ok) toast(action === 'approve' ? t('knowledge.view.24') : t('knowledge.view.28'));
      else toast(t('knowledge.view.kbDown'), 'warn');
    });
  };

  // ── 知识卡右键菜单（新增需求 2：项与顺序固定）──
  const cardMenu = (e: React.MouseEvent, card: KnowCard): void => {
    e.preventDefault();
    const retired = card.st === 'retired';
    const items: MenuEntry[] = [
      { icon: <IconEdit />, label: t('common.22'), onClick: () => setModal({ kind: 'rename', card }) },
      { icon: <IcScope />, label: t('knowledge.view.74'), onClick: () => setModal({ kind: 'scope', card }) },
      {
        icon: <IcEye />, label: t('knowledge.view.91'),
        onClick: () => useKnowledgeStore.getState().openPreview({
          kind: 'asset', card, projectId: card.projectId,
        }),
      },
      { icon: <IconReport />, label: t('knowledge.view.51'), onClick: () => setModal({ kind: 'history', card }) },
      '---',
      retired
        ? {
          icon: <IconRecover />, label: t('knowledge.view.37'),
          onClick: () => {
            void useKnowledgeStore.getState().override(card, { status: 'active' }).then((ok) => {
              if (ok) toast(t('knowledge.view.reenabled'));
              else toast(t('knowledge.view.restoreFailed'), 'warn');
            });
          },
        }
        : {
          icon: <IcRetire />, danger: true, label: t('knowledge.view.82'),
          onClick: () => setModal({ kind: 'retire', card }),
        },
      // ★ 新增需求 2④：「彻底删除」固定排在退役/恢复的正下方
      { icon: <IconTrash />, danger: true, label: t('context.menu.02'), onClick: () => setModal({ kind: 'purge', card }) },
    ];
    openMenu(items, e.clientX, e.clientY);
  };

  const openSkillDetail = (pack: SkillPack): void => {
    useKnowledgeStore.getState().openPreview({
      kind: 'skill', card: null, pack, projectId: pack.projectId,
    });
  };

  const doSkillReview = (pack: SkillPack, action: 'approve' | 'reject'): void => {
    void useKnowledgeStore.getState().reviewSkillpack(pack, action).then((ok) => {
      if (ok) toast(action === 'approve' ? t('knowledge.view.43') : t('knowledge.view.44'));
      else toast(t('knowledge.view.skillDown'), 'warn');
    });
  };

  const doSkillStatus = (pack: SkillPack, status: SkillPackStatus): void => {
    void useKnowledgeStore.getState().setSkillpackStatus(pack, status).then((ok) => {
      if (ok) {
        toast(status === 'active' ? t('knowledge.view.40')
          : status === 'pending' ? t('knowledge.view.41') : t('knowledge.view.42'));
      } else toast(t('knowledge.view.skillDown'), 'warn');
    });
  };

  /** 项目经验技能菜单：与知识卡菜单完全独立，按技能自己的三态策展。 */
  const projectSkillMenu = (e: React.MouseEvent, pack: SkillPack): void => {
    e.preventDefault();
    const detail: MenuEntry = { icon: <IcEye />, label: t('knowledge.view.52'), onClick: () => openSkillDetail(pack) };
    const items: MenuEntry[] = [detail, '---'];
    if (pack.status === 'pending') {
      items.push(
        { icon: <IconCheckbox />, label: t('knowledge.view.60'), onClick: () => doSkillReview(pack, 'approve') },
        { icon: <IconAlert />, danger: true, label: t('knowledge.view.92'), onClick: () => doSkillReview(pack, 'reject') },
      );
    } else if (pack.status === 'active') {
      items.push({
        icon: <IcRetire />, danger: true, label: t('knowledge.view.81'),
        onClick: () => confirmModal({
          title: t('knowledge.view.85'),
          body: t('knowledge.view.83'),
          okLabel: t('knowledge.view.81'), danger: true, onOk: () => doSkillStatus(pack, 'retired'),
        }),
      });
    } else {
      items.push(
        { icon: <IconRecover />, label: t('knowledge.view.37'), onClick: () => doSkillStatus(pack, 'active') },
        {
          icon: <IconTrash />, danger: true, label: t('context.menu.02'),
          onClick: () => confirmModal({
            title: t('knowledge.view.31'),
            body: t('knowledge.view.11'),
            okLabel: t('context.menu.02'), danger: true,
            onOk: () => {
              void useKnowledgeStore.getState().purgeSkillpack(pack).then((ok) => {
                if (ok) toast(t('knowledge.view.skillDeleted'));
                else toast(t('knowledge.view.skillDeleteFailed'), 'warn');
              });
            },
          }),
        },
      );
    }
    openMenu(items, e.clientX, e.clientY);
  };

  /** 第三方技能自己的菜单扩展点；不借用项目经验或知识图谱的菜单项。 */
  const thirdPartySkillMenu = (e: React.MouseEvent, pack: SkillPack): void => {
    e.preventDefault();
    const items: MenuEntry[] = [
      { icon: <IcEye />, label: t('knowledge.view.52'), onClick: () => openSkillDetail(pack) },
      '---',
    ];
    if (pack.status === 'active') {
      items.push(
        { icon: <IconReport />, label: t('knowledge.view.77'), onClick: () => doSkillStatus(pack, 'pending') },
        { icon: <IcRetire />, danger: true, label: t('knowledge.view.81'), onClick: () => doSkillStatus(pack, 'retired') },
      );
    } else if (pack.status === 'pending') {
      items.push(
        { icon: <IconRecover />, label: t('knowledge.view.19'), onClick: () => doSkillStatus(pack, 'active') },
        { icon: <IcRetire />, danger: true, label: t('knowledge.view.81'), onClick: () => doSkillStatus(pack, 'retired') },
      );
    } else {
      items.push(
        { icon: <IconRecover />, label: t('knowledge.view.37'), onClick: () => doSkillStatus(pack, 'active') },
        {
          icon: <IconTrash />, danger: true, label: t('knowledge.view.15'),
          onClick: () => confirmModal({
            title: t('knowledge.view.16'),
            body: t('knowledge.view.22'),
            okLabel: t('knowledge.view.14'), danger: true,
            onOk: () => {
              void useKnowledgeStore.getState().purgeSkillpack(pack).then((ok) => {
                if (ok) toast(t('knowledge.view.skillUnloaded'));
                else toast(t('knowledge.view.skillUnloadFailed'), 'warn');
              });
            },
          }),
        },
      );
    }
    openMenu(items, e.clientX, e.clientY);
  };

  const totalEmpty = loadedAt > 0 && cards.length === 0;
  const firstLoading = loading && loadedAt === 0 && !error;
  const firstError = !!error && loadedAt === 0;

  const scopeZh = scope === 'global' ? t('knowledge.preview.03') : scope === 'project' ? t('knowledge.preview.13') : null;
  const skillTitle: Record<SkillPane, string> = {
    root: t('knowledge.view.39'), system: t('knowledge.view.71'), builtin: t('knowledge.preview.12'),
    experience: t('knowledge.preview.14'), third: t('knowledge.preview.11'),
    mcpRoot: t('knowledge.view.mcpLabel'),
    mcpSystem: t('knowledge.view.mcpSystemLabel'),
    mcpThird: t('knowledge.view.mcpThirdLabel'),
  };
  const headTitle = pane === 'skills'
    ? skillTitle[skillPane]
    : (cat && scopeZh ? `${scopeZh} · ${cat}` : scopeZh ?? t('knowledge.view.67'));

  const graphDescriptions: Record<string, string> = {
    [t('knowledge.view.67')]: t('knowledge.view.45'),
    [t('knowledge.preview.03')]: t('knowledge.view.20'),
    [t('knowledge.preview.13')]: t('knowledge.view.17'),
    约定: t('knowledge.view.64'),
    坑: t('knowledge.view.25'),
    模式: t('knowledge.view.73'),
    清单: t('knowledge.view.75'),
  };
  const skillDescriptions: Record<SkillPane, string> = {
    root: t('knowledge.view.08'),
    system: t('knowledge.view.09'),
    builtin: t('knowledge.view.89'),
    experience: t('knowledge.view.65'),
    third: t('knowledge.view.63'),
    mcpRoot: t('knowledge.view.mcpRootDesc'),
    mcpSystem: t('knowledge.view.mcpSystemDesc'),
    mcpThird: t('knowledge.view.mcpThirdDesc'),
  };
  const headSub = pane === 'skills'
    ? skillDescriptions[skillPane]
    : graphDescriptions[cat ?? scopeZh ?? t('knowledge.view.67')];

  return (
    <>
      {/* ═══ 左侧导航（SidePanel）═══ */}
      <aside className="side">
        <div className="side-head"><div className="side-title">{t('knowledge.view.06')}</div></div>

        <div className="search-wrap">
          <div className="search">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.5" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" />
            </svg>
            <input
              placeholder={pane === 'skills' ? t('knowledge.view.47') : t('knowledge.view.48')}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label={pane === 'skills' ? t('knowledge.view.47') : t('knowledge.view.48')}
            />
          </div>
        </div>

        {/* v0.43：四态筛选始终在搜索框下，不因知识/技能面板切换而消失。 */}
        <div className="kn-status-filter">
          <div className="seg">
            {SEGS.map((s) => (
              <button
                key={s.label}
                className={seg === s.st ? 'on' : ''}
                onClick={() => setSeg(s.st)}
              >
                {t(s.label)}
              </button>
            ))}
          </div>
        </div>

        <div className="side-scroll">
          {/* ── L1 · 知识图谱 ── */}
          <div
            className={'navrow kn-l1' + (pane === 'graph' ? ' active' : '')}
            role="button" tabIndex={0}
            onClick={clickGraphL1}
            onKeyDown={(e) => { if (e.key === 'Enter') clickGraphL1(); }}
          >
            <span className="navrow-ic"><IconBook /></span>
            <span className="navrow-nm">{t('knowledge.view.67')}</span>
          </div>

          {expandGraph && SCOPES.map((s) => (
            <React.Fragment key={s.id}>
              {/* L2 · 全局 / 项目 —— 行点选，行尾小箭头折叠 L3 */}
              <div
                className={'navrow kn-l2' + (pane === 'graph' && scope === s.id && !cat ? ' active' : '')}
                role="button" tabIndex={0}
                onClick={() => clickScopeL2(s.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') clickScopeL2(s.id); }}
              >
                <span className="navrow-ic">{s.icon}</span>
                <span className="navrow-nm">{t(s.label)}</span>
                <span
                  className={'kn-chev tail' + (expandScope[s.id] ? ' open' : '')}
                  role="button" tabIndex={0}
                  aria-label={t('knowledge.view.toggleCategoryAria', {
                    action: expandScope[s.id] ? t('knowledge.view.46') : t('knowledge.view.23'),
                    label: t(s.label),
                  })}
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandScope((m) => ({ ...m, [s.id]: !m[s.id] }));
                  }}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return;
                    e.stopPropagation();
                    setExpandScope((m) => ({ ...m, [s.id]: !m[s.id] }));
                  }}
                >
                  <IconChevR />
                </span>
              </div>

              {/* L3 · 约定 / 坑 / 模式 / 清单 */}
              {expandScope[s.id] && CATS.map((c) => (
                <div
                  key={`${s.id}:${c.id}`}
                  className={'navrow kn-l3' + (pane === 'graph' && scope === s.id && cat === c.id ? ' active' : '')}
                  role="button" tabIndex={0}
                  onClick={() => clickCatL3(s.id, c.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter') clickCatL3(s.id, c.id); }}
                >
                  <span className="navrow-ic">{c.icon}</span>
                  <span className="navrow-nm">{c.id}</span>
                </div>
              ))}
            </React.Fragment>
          ))}

          {/* ── L1 · 技能包 ── */}
          <div
            className={'navrow kn-l1' + (pane === 'skills' && skillPane === 'root' ? ' active' : '')}
            role="button" tabIndex={0}
            onClick={clickSkillsL1}
            onKeyDown={(e) => { if (e.key === 'Enter') clickSkillsL1(); }}
          >
            <span className="navrow-ic"><IcPuzzle /></span>
            <span className="navrow-nm">{t('knowledge.view.skillsLabel')}</span>
            <span className="kn-badge">{t('knowledge.view.skillsWIP')}</span>
          </div>

          {expandSkills && (
            <>
              {/* L2 · 系统技能包；L3 再分系统自备 / 项目经验 */}
              <div
                className={'navrow kn-l2' + (pane === 'skills' && skillPane === 'system' ? ' active' : '')}
                role="button" tabIndex={0}
                onClick={() => { setPane('skills'); setSkillPane('system'); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { setPane('skills'); setSkillPane('system'); } }}
              >
                <span className="navrow-ic"><IcPuzzle /></span>
                <span className="navrow-nm">{t('knowledge.view.71')}</span>
                <span className="kn-count">
                  {skillpacks.systemBuiltin.length + skillpacks.projectExperience.length}
                </span>
                <span
                  className={'kn-chev tail' + (expandSystemSkills ? ' open' : '')}
                  role="button" tabIndex={0}
                  aria-label={t('knowledge.view.toggleSystemAria', {
                    action: expandSystemSkills ? t('knowledge.view.46') : t('knowledge.view.23'),
                  })}
                  onClick={(e) => { e.stopPropagation(); setExpandSystemSkills((v) => !v); }}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return;
                    e.stopPropagation();
                    setExpandSystemSkills((v) => !v);
                  }}
                >
                  <IconChevR />
                </span>
              </div>

              {expandSystemSkills && (
                <>
                  <div
                    className={'navrow kn-l3' + (pane === 'skills' && skillPane === 'builtin' ? ' active' : '')}
                    role="button" tabIndex={0}
                    onClick={() => { setPane('skills'); setSkillPane('builtin'); }}
                    onKeyDown={(e) => { if (e.key === 'Enter') { setPane('skills'); setSkillPane('builtin'); } }}
                  >
                    <span className="navrow-ic"><IcPuzzle /></span>
                    <span className="navrow-nm">{t('knowledge.preview.12')}</span>
                    <span className="kn-count">{skillpacks.systemBuiltin.length}</span>
                  </div>
                  <div
                    className={'navrow kn-l3' + (pane === 'skills' && skillPane === 'experience' ? ' active' : '')}
                    role="button" tabIndex={0}
                    onClick={() => { setPane('skills'); setSkillPane('experience'); }}
                    onKeyDown={(e) => { if (e.key === 'Enter') { setPane('skills'); setSkillPane('experience'); } }}
                  >
                    <span className="navrow-ic"><IconSpark /></span>
                    <span className="navrow-nm">{t('knowledge.preview.14')}</span>
                    <span className="kn-count">{skillpacks.projectExperience.length}</span>
                  </div>
                </>
              )}

              {/* L2 · 第三方技能包；安装入口先作为真实空口保留 */}
              <div
                className={'navrow kn-l2' + (pane === 'skills' && skillPane === 'third' ? ' active' : '')}
                role="button" tabIndex={0}
                onClick={() => { setPane('skills'); setSkillPane('third'); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { setPane('skills'); setSkillPane('third'); } }}
              >
                <span className="navrow-ic"><IcPuzzle /></span>
                <span className="navrow-nm">{t('knowledge.preview.11')}</span>
                {skillpacks.thirdParty.length > 0 && <span className="kn-count">{skillpacks.thirdParty.length}</span>}
              </div>
            </>
          )}

          {/* ── L1 · 技能插座（MCP）——v1.0.23.6 无 chev UI：ic 顶到最左，交互不变 ── */}
          <div
            className={'navrow kn-l1' + (pane === 'skills' && skillPane === 'mcpRoot' ? ' active' : '')}
            role="button" tabIndex={0}
            onClick={clickMcpL1}
            onKeyDown={(e) => { if (e.key === 'Enter') clickMcpL1(); }}
          >
            <span className="navrow-ic"><IcPlug /></span>
            <span className="navrow-nm">{t('knowledge.view.mcpLabel')}</span>
            <span className="kn-badge">{t('knowledge.view.skillsWIP')}</span>
          </div>

          {expandMcp && (
            <>
              {/* L2 · 系统技能插座 */}
              <div
                className={'navrow kn-l2' + (pane === 'skills' && skillPane === 'mcpSystem' ? ' active' : '')}
                role="button" tabIndex={0}
                onClick={() => { setPane('skills'); setSkillPane('mcpSystem'); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { setPane('skills'); setSkillPane('mcpSystem'); } }}
              >
                <span className="navrow-ic"><IcPlug /></span>
                <span className="navrow-nm">{t('knowledge.view.mcpSystemLabel')}</span>
              </div>
              {/* L2 · 第三方技能插座 */}
              <div
                className={'navrow kn-l2' + (pane === 'skills' && skillPane === 'mcpThird' ? ' active' : '')}
                role="button" tabIndex={0}
                onClick={() => { setPane('skills'); setSkillPane('mcpThird'); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { setPane('skills'); setSkillPane('mcpThird'); } }}
              >
                <span className="navrow-ic"><IcPlug /></span>
                <span className="navrow-nm">{t('knowledge.view.mcpThirdLabel')}</span>
              </div>
            </>
          )}
        </div>

        {/* 底部计数：按面板换口径 */}
        <div>
          <div style={{ padding: '8px 20px', fontSize: 11, color: 'var(--ink-3)' }}>
            {pane === 'skills'
              ? <>
                {t('knowledge.view.72')} {skillpacks.systemBuiltin.length} · {t('knowledge.view.90')} {skillpacks.projectExperience.length}
                {' '}· {t('knowledge.view.69')} {skillpacks.thirdParty.length}
              </>
              : <>{t('knowledge.view.monthSummary', { n: cards.length, m: monthNew })}</>}
          </div>
        </div>
      </aside>

      {/* ═══ 右侧内容区（Stage；kn-stage 给预览面板一个定位锚）═══ */}
      <div className="stage kn-stage">
        <div className="stage-card">
          <div className="stage-head kn-head">
            <div>
              <div className="stage-h1">{headTitle}</div>
              <div className="stage-sub">{headSub}</div>
            </div>
            {/* 画像层入口：看全局知识时点亮（PROFILE.md 常驻偏好档案，预览面板打开） */}
            {pane === 'graph' && scope === 'global' && profileProjects.length > 0 && (
              <div className="kn-head-side">
                {profileProjects.map((pid) => (
                  <span
                    key={pid}
                    className="lk"
                    role="button" tabIndex={0}
                    title={t('knowledge.view.66')}
                    onClick={() => useKnowledgeStore.getState().openPreview({
                      kind: 'profile', card: null, projectId: pid,
                    })}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        useKnowledgeStore.getState().openPreview({
                          kind: 'profile', card: null, projectId: pid,
                        });
                      }
                    }}
                  >
                    {t('knowledge.view.profileTitle', { proj: projName(pid) })}
                  </span>
                ))}
              </div>
            )}
            {pane === 'skills' && skillPane === 'third' && (
              <div className="kn-head-side">
                <button
                  type="button"
                  className="kn-install-placeholder"
                  disabled
                  title={t('knowledge.view.70')}
                >
                  {t('knowledge.view.installComing')}
                </button>
              </div>
            )}
          </div>

          {pane === 'graph' && pending > 0 && (
            <div className="notice">
              <IconSpark />
              <span>{t('knowledge.view.pendingSuggestions', { n: pending })}</span>
              <span
                className="lk"
                role="button" tabIndex={0}
                onClick={() => setSeg('pending')}
                onKeyDown={(e) => { if (e.key === 'Enter') setSeg('pending'); }}
              >
                {t('knowledge.view.goReview')}
              </span>
            </div>
          )}
          {pane === 'skills' && pendingSkills > 0 && (
            <div className="notice">
              <IconSpark />
              <span>{t('knowledge.view.pendingSkills', { n: pendingSkills })}</span>
              <span
                className="lk"
                role="button" tabIndex={0}
                onClick={() => setSeg('pending')}
                onKeyDown={(e) => { if (e.key === 'Enter') setSeg('pending'); }}
              >
                {t('knowledge.view.goReview')}
              </span>
            </div>
          )}

          <div className="stage-scroll" style={{ paddingTop: 4 }}>
            {pane === 'skills' ? (
              skillPane === 'mcpRoot' || skillPane === 'mcpSystem' || skillPane === 'mcpThird' ? (
                <div className="empty2">
                  <div className="e-ic"><PlugBig /></div>
                  <p>
                    {skillPane === 'mcpRoot'
                      ? t('knowledge.view.mcpRootEmpty')
                      : skillPane === 'mcpSystem'
                        ? t('knowledge.view.mcpSystemEmpty')
                        : t('knowledge.view.mcpThirdEmpty')}
                  </p>
                </div>
              ) : shownPacks.length === 0 ? (
                <div className="empty2">
                  <div className="e-ic"><IcPuzzle /></div>
                  <p>
                    {kw || seg
                      ? t('knowledge.view.58')
                      : skillPane === 'builtin'
                        ? t('knowledge.view.57')
                        : skillPane === 'experience'
                          ? t('knowledge.view.80')
                          : skillPane === 'third'
                            ? t('knowledge.view.78')
                            : t('knowledge.view.30')}
                  </p>
                </div>
              ) : (
                shownPacks.map((p) => {
                  const kindLabel = p.kind === 'system_builtin'
                    ? t('knowledge.view.72') : p.kind === 'project_experience' ? t('knowledge.view.90') : t('knowledge.view.69');
                  const chipClass = p.kind === 'system_builtin'
                    ? 'info' : p.kind === 'project_experience' ? 'acc' : 'ok';
                  const statusTitle = p.status === 'active'
                    ? t('knowledge.view.61')
                    : p.status === 'pending'
                      ? t('knowledge.view.33')
                      : t('knowledge.view.26');
                  const sourceText = p.kind === 'system_builtin'
                    ? t('knowledge.view.88')
                    : p.kind === 'project_experience'
                      ? t('knowledge.view.exportFrom', { proj: projName(p.projectId), id: p.assetId || '—' })
                      : t('knowledge.view.62');
                  return (
                    <div
                      key={p.packId}
                      className={'card kn-skill' + (p.immutable ? ' immutable' : '')}
                      onContextMenu={(e) => {
                        if (p.kind === 'system_builtin') {
                          e.preventDefault(); // 系统自备技能：连原生菜单也不弹
                        } else if (p.kind === 'project_experience') {
                          projectSkillMenu(e, p);
                        } else {
                          thirdPartySkillMenu(e, p);
                        }
                      }}
                    >
                      <div className="card-top">
                        <span className="navrow-ic kn-skill-ic"><IcPuzzle /></span>
                        <span className={`chip ${chipClass}`}>{kindLabel}</span>
                        <div className="card-title" title={p.name}>{p.name}</div>
                        {p.immutable && <span className="kn-immutable">{t('knowledge.preview.09')}</span>}
                        <div
                          className={'card-status ' + (p.st === 'ok' ? 'ok' : p.st === 'pending' ? 'pend' : 'retired')}
                          title={statusTitle}
                        />
                      </div>
                      {p.description && <div className="card-body mut">{p.description}</div>}
                      <div className="card-meta kn-skill-meta">
                        <span>{sourceText}</span>
                        <span className="sep">·</span>
                        <span>{p.scope === 'global' ? t('knowledge.preview.02') : t('knowledge.preview.06')}</span>
                        <span className="sep">·</span>
                        <span title={p.packId} className="kn-pack-id">{p.packId}</span>
                        {p.createdAt && (
                          <>
                            <span className="sep">·</span>
                            <span>{t('knowledge.view.registeredAt', { date: knowDateLabel(p.createdAt) })}</span>
                          </>
                        )}
                      </div>

                      {p.kind === 'project_experience' && p.status === 'pending' && (
                        <div className="kn-review">
                          <div className="kn-flags">
                            <span className="kn-flag acc">{t('knowledge.view.54')}</span>
                          </div>
                          <div className="kn-review-acts">
                            <button className="kn-btn ok" onClick={() => doSkillReview(p, 'approve')}>{t('knowledge.view.87')}</button>
                            <button
                              className="kn-btn"
                              title={t('knowledge.view.93')}
                              onClick={() => doSkillReview(p, 'reject')}
                            >
                              {t('knowledge.view.92')}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              )
            ) : firstLoading ? (
              <div className="empty2">
                <div className="e-ic"><BookBig /></div>
                <p>{t('knowledge.view.56')}</p>
              </div>
            ) : firstError ? (
              <div className="empty2">
                <div className="e-ic"><BookBig /></div>
                <p>
                  {t('knowledge.view.kbNotReady', { error })}{' '}
                  <span
                    className="lk"
                    role="button" tabIndex={0}
                    onClick={() => void useKnowledgeStore.getState().load()}
                    onKeyDown={(e) => { if (e.key === 'Enter') void useKnowledgeStore.getState().load(); }}
                  >
                    {t('common.03')}
                  </span>
                </p>
              </div>
            ) : shown.length === 0 ? (
              <div className="empty2">
                <div className="e-ic"><BookBig /></div>
                <p>
                  {totalEmpty
                    ? t('knowledge.view.79')
                    : t('knowledge.view.59')}
                </p>
              </div>
            ) : (
              shown.map((k) => {
                const primary = k.evidence[0];
                const srcReport = primary ? sourceLabel(primary.sourceRef) : t('knowledge.view.01');
                const scopeLabel = k.scope === 'global' ? t('knowledge.view.13') : t('knowledge.view.projectScope', { proj: projName(k.projectId) });
                const dotTitle = k.st === 'ok'
                  ? (k.status === 'core' ? t('knowledge.view.55') : t('knowledge.preview.10'))
                  : k.st === 'pending'
                    ? (k.needsReview ? t('knowledge.view.35')
                      : k.retireSuggested ? t('knowledge.view.36')
                        : t('knowledge.view.34'))
                    : t('knowledge.view.27');
                return (
                  <div key={k.id} className="card" onContextMenu={(e) => cardMenu(e, k)}>
                    <div className="card-top">
                      <span className={`chip ${k.chip}`}>{k.cat}</span>
                      <div className="card-title" title={`${k.clsZh || k.cls} · ${k.title}`}>
                        {k.title}
                      </div>
                      {k.status === 'core' && <span className="kn-core">{t('knowledge.preview.07')}</span>}
                      <div
                        className={'card-status ' + (k.st === 'ok' ? 'ok' : k.st === 'pending' ? 'pend' : 'retired')}
                        title={dotTitle}
                      />
                    </div>
                    <div className="card-body mut">{k.body}</div>
                    <div className="card-meta">
                      <span>{scopeLabel}</span>
                      <span className="sep">·</span>
                      <span>
                        来自{' '}
                        <span
                          className="lk"
                          role="button" tabIndex={0}
                          title={t('knowledge.view.76')}
                          onClick={() => jumpToSource(k)}
                          onKeyDown={(e) => { if (e.key === 'Enter') jumpToSource(k); }}
                        >
                          {projName(k.projectId)} · {agentName(k.projectId, primary?.agentId ?? null)}
                        </span>
                        {' '}的 {srcReport}
                      </span>
                      <span className="sep">·</span>
                      <span title={t('knowledge.view.sourceCount', { n: k.sourceCount })}>{t('knowledge.view.citeCount', { n: k.cites })}</span>
                      <span className="sep">·</span>
                      <span>{t('knowledge.view.firstSeenAt', { date: knowDateLabel(k.firstSeen) })}</span>
                    </div>

                    {/* 待审卡：徽标 + 内联策展按钮（批准入库 / 驳回退役） */}
                    {k.st === 'pending' && (
                      <div className="kn-review">
                        <div className="kn-flags">
                          {k.needsReview && (
                            <span
                              className="kn-flag warn"
                              title={k.conflictWith.length
                                ? `与 ${k.conflictWith.join('、')} 冲突`
                                : t('knowledge.view.50')}
                            >
                              {t('knowledge.view.conflictPending')}
                            </span>
                          )}
                          {k.retireSuggested && <span className="kn-flag mut">{t('knowledge.view.29')}</span>}
                          {!k.needsReview && !k.retireSuggested && (
                            <span className="kn-flag acc">{t('knowledge.view.49')}</span>
                          )}
                        </div>
                        <div className="kn-review-acts">
                          <button className="kn-btn ok" onClick={() => doReview(k, 'approve')}>{t('knowledge.view.38')}</button>
                          <button
                            className="kn-btn"
                            title={t('knowledge.view.94')}
                            onClick={() => doReview(k, 'reject')}
                          >
                            {t('knowledge.view.92')}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 「预览」右滑面板：挂在 kn-stage 内，浮在卡片列表右侧之上（不挤布局） */}
        <KnowledgePreview />
      </div>

      <KnowledgeModals modal={modal} onClose={() => setModal(null)} />
    </>
  );
};

// ═══════════════════════════════════════════════════════════════
// 弹窗宿主：重命名 / 调整范围（雾化）/ 退役 / 彻底删除 / 引用历史。
// scrim/modal/btn 用全局既有类（openInputModal 同源）；调整范围叠 .kn-scrim-blur。
// ═══════════════════════════════════════════════════════════════

const KnowledgeModals: React.FC<{ modal: ModalState; onClose: () => void }> = ({ modal, onClose }) => {
  const { t } = useTranslation();
  const [title, setTitle] = useState('');
  const [scopeSel, setScopeSel] = useState<KnowScope>('project');
  const [catSel, setCatSel] = useState<KnowCat>('模式');
  const [hist, setHist] = useState<KnowAssetDetail | null>(null);
  const [histLoading, setHistLoading] = useState(false);

  // 打开弹窗时装入当前卡片内容
  useEffect(() => {
    if (!modal) return undefined;
    if (modal.kind === 'rename') setTitle(modal.card.title);
    if (modal.kind === 'scope') { setScopeSel(modal.card.scope); setCatSel(modal.card.cat); }
    if (modal.kind === 'history') {
      setHist(null);
      setHistLoading(true);
      let alive = true;
      void useKnowledgeStore.getState().fetchDetail(modal.card).then((d) => {
        if (!alive) return;
        setHist(d);
        setHistLoading(false);
      });
      return () => { alive = false; };
    }
    return undefined;
  }, [modal]);

  if (!modal) return null;
  const { card } = modal;

  const doRename = (): void => {
    const nt = title.trim();
    if (!nt) { toast(t('knowledge.view.titleEmpty'), 'warn'); return; }
    onClose();
    if (nt === card.title) return;
    void useKnowledgeStore.getState().override(card, { title: nt }).then((ok) => {
      if (ok) toast(t('knowledge.view.renamed'));
      else toast(t('knowledge.view.renameFailed'), 'warn');
    });
  };

  const doScope = (): void => {
    onClose();
    if (scopeSel === card.scope && catSel === card.cat) return;
    void useKnowledgeStore.getState().override(card, { scope: scopeSel, category: catSel }).then((ok) => {
      if (ok) toast(t('knowledge.view.scopeAdjusted'));
      else toast(t('knowledge.view.scopeAdjustFailed'), 'warn');
    });
  };

  const doRetire = (): void => {
    onClose();
    void useKnowledgeStore.getState().override(card, { status: 'retired' }).then((ok) => {
      if (ok) toast(t('knowledge.preview.04'));
      else toast(t('knowledge.view.retireFailed'), 'warn');
    });
  };

  const doPurge = (): void => {
    onClose();
    void useKnowledgeStore.getState().purge(card).then((ok) => {
      if (ok) toast(t('knowledge.view.deleted'));
      else toast(t('knowledge.view.deleteFailed'), 'warn');
    });
  };

  return (
    <div
      className={'scrim center' + (modal.kind === 'scope' ? ' kn-scrim-blur' : '')}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {modal.kind === 'rename' && (
        <div className="modal">
          <div className="modal-title">{t('common.22')}</div>
          <div className="kn-edit-label">{t('knowledge.view.53')}</div>
          <input
            className="modal-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t('knowledge.view.68')}
            aria-label={t('knowledge.view.68')}
          />
          <div className="kn-hint">{t('knowledge.view.18')}</div>
          <div className="modal-acts">
            <button className="btn btn-ghost" style={{ flex: 'none', padding: '0 20px' }} onClick={onClose}>
              {t('chat.stream.03')}
            </button>
            <button className="btn btn-primary" style={{ flex: 'none', padding: '0 20px' }} onClick={doRename}>
              {t('context.menu.11')}
            </button>
          </div>
        </div>
      )}

      {modal.kind === 'scope' && (
        <div className="modal">
          <div className="modal-title">{t('knowledge.view.74')}</div>
          <div className="modal-body">
            二级决定这条知识随谁复用：<b>全局</b>会注入你所有项目的项目经理与成员，
            <b>项目</b>只在来源项目内生效；三级是价值判断（坑还是约定、模式还是清单），
            决定注入时的呈现与权重。保存后 Harness 的注入、画像与技能导出即刻按新值走。
          </div>

          <div className="kn-edit-label">{t('knowledge.view.12')}</div>
          <div className="seg kn-seg-block">
            {SCOPES.map((s) => (
              <button
                key={s.id}
                className={scopeSel === s.id ? 'on' : ''}
                onClick={() => setScopeSel(s.id)}
              >
                {t(s.label)}
              </button>
            ))}
          </div>

          <div className="kn-edit-label">{t('knowledge.view.10')}</div>
          <div className="kn-cat-row">
            {CATS.map((c) => (
              <button
                key={c.id}
                className={'kn-cat-pick' + (catSel === c.id ? ' sel' : '')}
                onClick={() => setCatSel(c.id)}
              >
                {c.icon}
                <span>{c.id}</span>
              </button>
            ))}
          </div>

          <div className="modal-acts">
            <button className="btn btn-ghost" style={{ flex: 'none', padding: '0 20px' }} onClick={onClose}>
              {t('chat.stream.03')}
            </button>
            <button className="btn btn-primary" style={{ flex: 'none', padding: '0 20px' }} onClick={doScope}>
              {t('context.menu.11')}
            </button>
          </div>
        </div>
      )}

      {modal.kind === 'retire' && (
        <div className="modal">
          <div className="modal-title">{t('knowledge.view.86')}</div>
          <div className="modal-body">{t('knowledge.view.84')}</div>
          <div className="modal-acts">
            <button className="btn btn-ghost" style={{ flex: 'none', padding: '0 20px' }} onClick={onClose}>
              {t('chat.stream.03')}
            </button>
            <button
              className="btn btn-primary"
              style={{ flex: 'none', padding: '0 20px', background: 'var(--danger)' }}
              onClick={doRetire}
            >
              {t('knowledge.view.81')}
            </button>
          </div>
        </div>
      )}

      {modal.kind === 'purge' && (
        <div className="modal">
          <div className="modal-title">{t('knowledge.view.32')}</div>
          <div className="modal-body">
            {t('knowledge.view.deleteIrreversible')} {t('knowledge.view.retireHint')}
          </div>
          <div className="modal-acts">
            <button className="btn btn-ghost" style={{ flex: 'none', padding: '0 20px' }} onClick={onClose}>
              {t('chat.stream.03')}
            </button>
            <button
              className="btn btn-primary"
              style={{ flex: 'none', padding: '0 20px', background: 'var(--danger)' }}
              onClick={doPurge}
            >
              {t('context.menu.02')}
            </button>
          </div>
        </div>
      )}

      {modal.kind === 'history' && (
        <div className="modal">
          <div className="modal-title">{t('knowledge.view.citeHistoryTitle', { n: card.cites })}</div>
          <div className="modal-body">
            {t('knowledge.view.traceHelp')}
          </div>

          <div className="kn-edit-label">{t('knowledge.preview.01')}</div>
          <div className="kn-hist">
            {histLoading ? (
              <div className="kn-hist-row"><div className="kn-hist-top"><b>{t('knowledge.preview.08')}</b></div></div>
            ) : !hist || hist.usage.length === 0 ? (
              <div className="kn-hist-row">
                <div className="kn-hist-top"><b>{t('knowledge.view.96')}</b></div>
              </div>
            ) : (
              hist.usage.map((u, i) => (
                <div className="kn-hist-row" key={`${u.kind}-${u.step}-${i}`}>
                  <div className="kn-hist-top">
                    <b>{USAGE_ZH[u.kind] || u.kind}</b>
                    {typeof u.step === 'number' && <span>{t('knowledge.view.stepCount', { n: u.step })}</span>}
                    <span className="kn-hist-date">{knowDateLabel(u.at)}</span>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="kn-edit-label">{t('knowledge.view.evidenceLabel', { n: card.evidence.length })}</div>
          <div className="kn-hist">
            {card.evidence.length === 0 ? (
              <div className="kn-hist-row">
                <div className="kn-hist-top"><b>{t('knowledge.view.95')}</b></div>
              </div>
            ) : (
              card.evidence.map((ev, i) => (
                <div className="kn-hist-row" key={`${ev.sourceRef}-${i}`}>
                  <div className="kn-hist-top">
                    <b>{sourceLabel(ev.sourceRef)}</b>
                    <span>
                      {ev.sourceKind === 'instruction' ? t('knowledge.view.03')
                        : ev.sourceKind === 'report' ? t('common.08')
                          : ev.sourceKind === 'approval' ? t('knowledge.view.02') : ev.sourceKind}
                    </span>
                    {typeof ev.step === 'number' && <span>{t('knowledge.view.stepCount', { n: ev.step })}</span>}
                    <span className="kn-hist-date">{knowDateLabel(ev.observedAt)}</span>
                  </div>
                  {ev.excerpt && <div className="kn-hist-excerpt">{ev.excerpt}</div>}
                </div>
              ))
            )}
          </div>

          <div className="modal-acts">
            <button className="btn btn-ghost" style={{ flex: 'none', padding: '0 20px' }} onClick={onClose}>
              {t('app.01')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default KnowledgeView;
