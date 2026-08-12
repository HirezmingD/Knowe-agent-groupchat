/**
 * ConvList.tsx — 会话列表侧栏（component-tree §B · ChatListSidebar + ConvListItem）
 *
 * DOM：aside.clist > (.clist-head > .wordmark + button.icon-btn)
 *                  + (.search-wrap > .search > svg + input)
 *                  + (.clist-scroll > .citem[×N])
 *
 * .citem > (.cav > .avatar) + (.cbody > .cname + .cprev) + (.cmeta > .ctime.tnum + 徽标)
 *
 * 数据：selectProjectList / selectActiveProjectId + switchProject（唯一入口）。
 */

import React, { useState, useEffect } from 'react';
import { shallow } from 'zustand/shallow';
import { useCachedT } from '../i18n';
import i18n from '../i18n';
import { roleLabel } from '../shared/roleLabel';
import { useKnoweStore } from '../store/store';
import { useSettingsStore } from '../store/settings';
import { selectProjectList, selectActiveProjectId, orderRosterMembers, type WorkingAgentSummary } from '../store/selectors';
import { Avatar, AvatarGrid, palOf, glyphOf, type GridMember } from './Avatar';
import { PLATFORM_PROJECT_ID, ZINNIA_AVATAR, getZinniaDisplayName } from '../store/avatar';
import { isPrivateChat, isAgentDm, dmGroupOf, dmAgentOf } from '../store/chat';
import type { Member } from '../store/state';
import { useDirectoryPending } from '../store/directoryRecovery';
import { IconPlus, IconSearch, IconChevR } from './icons';
import { openAgentMenu, openConversationMenu } from './ContextMenu';
import NewProjectModal from './NewProjectModal';
import ResizeHandle from './ResizeHandle';
import {
  GlobalSearchResults, useGlobalSearchGroups, type GlobalSearchTarget,
} from './GlobalSearch';

export interface ConvListProps {
  onSearchNavigate?: (target: GlobalSearchTarget) => void;
}

const ignoreSearchNavigation = (target: GlobalSearchTarget): void => { void target; };

export const ConvList: React.FC<ConvListProps> = ({
  onSearchNavigate = ignoreSearchNavigation,
}) => {
  const { t } = useCachedT();
  // [v1.0.24.6-P1a] selectProjectList 每次遍历重建数组（任何事件都触发）→
  // shallow 比较：输出字段（名称/草稿/busy 成员）没变 → 左栏不重渲染。
  // 流式事件（stream_delta 每秒几十条）不再拉着左栏重排——左栏不显示消息内容。
  const projects = useKnoweStore(selectProjectList, shallow);
  // [v1.0.24.6-P1a] 原 `s.convs` 整树订阅（仅用于 pinnedHasUnread）→ 移除，
  // 改精准 selector（见下方 pinnedHasUnread）：任何项目任何事件不再触发主体重渲染。
  const pinnedProjects = useKnoweStore((s) => s.pinnedProjects);
  const mutedProjects = useKnoweStore((s) => s.mutedProjects);
  const foldedProjects = useKnoweStore((s) => s.foldedProjects);
  const pinnedCollapsed = useKnoweStore((s) => s.pinnedCollapsed);
  const foldedOpen = useKnoweStore((s) => s.foldedOpen);
  const togglePinnedCollapsed = useKnoweStore((s) => s.togglePinnedCollapsed);
  const toggleFoldedOpen = useKnoweStore((s) => s.toggleFoldedOpen);
  const activeId = useKnoweStore(selectActiveProjectId);
  const switchProject = useKnoweStore((s) => s.switchProject);
  const enterDm = useKnoweStore((s) => s.enterDm);

  /* [v1.0.19.2] 左上角 Logo 跟随主题：深色用暗色版，浅色用原版。 */
  const appearance = useSettingsStore((s) => s.appearance);

  const [q, setQ] = useState('');
  const [modalOpen, setModalOpen] = useState(false);

  /*
   * [v0.37] 群内私聊「面板模式」。
   *
   *   当前会话是 dm:{group}:{agent} → 左栏变形为「知知 + 该群（置顶高亮）+ 该群成员（缩进）」。
   *   哪个群、私聊的是谁，全从 activeId 解析（chat.ts 一处判定），不新增 store 状态。
   */
  const dmMode = isAgentDm(activeId);
  const dmGroup = dmGroupOf(activeId);
  const dmAgent = dmAgentOf(activeId);

  // 该群的成员（含忙碌态，实时）——只在私聊模式下订阅，按花名册同一顺序排。
  // [v1.0.24.6-P1a] shallow：返回数组成员字段没变时不重渲染（原每次重建新数组）。
  const dmMembers = useKnoweStore((s): Member[] => {
    if (!dmGroup) return [];
    const ms = (s.convs[dmGroup]?.members ?? []).filter((m) => m.status !== 'removed');
    return orderRosterMembers(ms);
  }, shallow);
  const dmGroupName = useKnoweStore(
    (s) => (dmGroup ? s.convs[dmGroup]?.projectName || dmGroup : ''),
  );

  /*
   * [v0.10b Bug3] ★ 一次性预热所有群聊头像。
   *   打开软件时，把左栏每个群宫格要用到的头像 URL 全部丢进 new Image() 预加载，
   *   浏览器缓存一命中，后面 <img> 渲染就是即时的——不再「打开那一刻才逐个发请求、
   *   逐个亮起来」。读 getState() 拿全量 convs（不新增订阅）；projects 变了就再跑
   *   一遍（幂等：已缓存的 URL 再 new Image() 也是空操作）。会话内新加的成员由
   *   Avatar 组件自身的预加载兜住（见 Avatar.tsx）。
   */
  useEffect(() => {
    const convs = useKnoweStore.getState().convs;
    const urls = new Set<string>();
    for (const pid of Object.keys(convs)) {
      for (const m of convs[pid]?.members ?? []) {
        if (m.status !== 'removed' && m.display.avatarUrl) urls.add(m.display.avatarUrl);
      }
    }
    urls.forEach((u) => { const im = new Image(); im.src = u; });
  }, [projects]);

  const searching = q.trim().length > 0;
  const searchGroups = useGlobalSearchGroups(q);

  // [v0.44.8] store 已维护好顺序；渲染层只负责拆成置顶、普通、折叠三段。
  const pinned = projects.filter((p) => !!pinnedProjects[p.id]);
  const folded = projects.filter((p) => !!foldedProjects[p.id]);
  const normal = projects.filter((p) => !pinnedProjects[p.id] && !foldedProjects[p.id]);
  const pinnedCount = pinned.length;
  const foldedCount = folded.length;
  // [v1.0.24.6-P1a] 精准订阅：只有「置顶项目从未读→有未读（或反向）」才触发重渲染。
  // 原来订阅整棵 convs 树（任何事件都重渲染主体），此处只关心未读布尔值。
  const pinnedHasUnread = useKnoweStore(
    (s) => Object.keys(s.pinnedProjects).some((pid) => (s.convs[pid]?.unread ?? 0) > 0),
  );

  return (
    <aside className="clist">
      <div className="clist-head">
        {/* [v0.5b #1] Logo 的正确位置：左栏顶上、搜索栏上面（原来是「Knowe」四个字）。
            上一批我把它放到 Rail 最底下了，是我理解错了。 */}
        {/* [v0.8c #6] 换成 v4：**真正透明的 PNG**（v3 是 RGB 白底，靠 mix-blend-mode
            擦背景——擦不干净，浅色底上仍能看出一块淡淡的矩形）。
            v4 用 tools/make-logo-v4.py 从 v3 生成：去白、反预乘羽化边缘、裁掉留白。
            现在它在浅色和深色主题下都是干净的，CSS 里的混色也可以撤了。 */}
        <div className="wordmark">
          <img
            src={appearance === 'dark' ? './brand/knowe-logo-v4-dark.png' : './brand/knowe-logo-v4.png'}
            alt="Knowe"
          />
        </div>
        <button
          className="icon-btn"
          data-tip={t('conv.list.07')}
          aria-label={t('conv.list.02')}
          onClick={() => setModalOpen(true)}
        >
          <IconPlus />
        </button>
      </div>

      <div className="search-wrap">
        <div className="search">
          <IconSearch />
          <input
            type="text"
            placeholder={t('conv.list.06')}
            aria-label={t('conv.list.01')}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </div>

      <div
        className={'clist-scroll clist-anim-freeze' + (searching ? ' clist-search-results' : '')}
        data-knowe-native-conversation-list="true"
        role={searching ? 'listbox' : undefined}
      >
        {searching ? (
          <GlobalSearchResults
            query={q}
            groups={searchGroups}
            onSelect={(target) => {
              setQ('');
              onSearchNavigate(target);
            }}
          />
        ) : (
          <>
        {/*
          [v0.4] 知知：**永远在最上面，不可删除、不可归档、搜不掉。**
          [v0.37] 私聊面板模式下她也雷打不动置顶——只是不再高亮（当前在某个群内私聊里）。
        */}
        <ConvListItem
          id={PLATFORM_PROJECT_ID}
          name={getZinniaDisplayName()}
          avatarUrl={ZINNIA_AVATAR}
          subtitle={t('conv.list.05')}
          active={activeId === PLATFORM_PROJECT_ID}
          pinned
          onOpen={() => switchProject(PLATFORM_PROJECT_ID)}
        />

        {dmMode && dmGroup ? (
          /*
           * [v0.37] 群内私聊面板：知知下方置顶「该群」，其下缩进列出群成员。
           *   单击群头像 → 退出私聊回到群聊；单击/双击成员 → 切到该成员的私聊。
           *   整块用 CSS 做进入动画（--ease-out / --dur-enter），丝滑不闪。
           */
          <div className="dm-panel">
            <ConvListItem
              key={dmGroup}
              id={dmGroup}
              name={dmGroupName}
              active                              /* 当前所在群 → 高亮置顶 */
              pinned
              onOpen={() => switchProject(dmGroup)}   /* 单击群头像 = 退出私聊 */
            />
            <div className="dm-members" role="list">
              {dmMembers.map((m) => (
                <DmMemberRow
                  key={m.id}
                  member={m}
                  active={m.id === dmAgent}
                  onOpen={() => enterDm(dmGroup, m.id)}
                />
              ))}
            </div>
          </div>
        ) : (
          <>
            {pinnedCount > 0 && (
              <>
                {pinnedCount >= 3 && (
                  <div
                    className={'fold-entry' + (!pinnedCollapsed ? ' open' : '')}
                    data-knowe-conv-entry="pinned"
                    role="button"
                    tabIndex={0}
                    aria-expanded={!pinnedCollapsed}
                    onClick={togglePinnedCollapsed}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') togglePinnedCollapsed();
                    }}
                  >
                    <span className="chev"><IconChevR /></span>
                    <span className="lbl">{t('conv.list.pinnedCount', { n: pinnedCount })}</span>
                    {pinnedCollapsed && pinnedHasUnread && (
                      <span className="knowe-conv-fold-news">{t('context.menu.01')}</span>
                    )}
                  </div>
                )}
                {pinned.map((p) => (
                  <ConvListItem
                    key={p.id}
                    id={p.id}
                    name={p.name}
                    draft={p.draft}
                    workingAgents={p.workingAgents}
                    active={p.id === activeId}
                    menuPinned
                    menuMuted={!!mutedProjects[p.id]}
                    data-knowe-menu-hidden={pinnedCount >= 3 && pinnedCollapsed}
                    onOpen={() => switchProject(p.id)}
                  />
                ))}
              </>
            )}

            {/* [v0.8b #11] 分组标签：上面是平台，下面是「项目」（设计稿 §会话列表） */}
            <div className="grp-label">{t('common.14')}</div>

            {normal.length === 0 ? (
              <div className="grp-empty">{t('conv.list.09')}</div>
            ) : normal.map((p) => (
              <ConvListItem
                key={p.id}
                id={p.id}
                name={p.name}
                draft={p.draft}
                workingAgents={p.workingAgents}
                active={p.id === activeId}
                menuMuted={!!mutedProjects[p.id]}
                onOpen={() => switchProject(p.id)}
              />
            ))}

            {foldedCount > 0 && (
              <>
                <div
                  className={'fold-entry' + (foldedOpen ? ' open' : '')}
                  data-knowe-conv-entry="folded"
                  role="button"
                  tabIndex={0}
                  aria-expanded={foldedOpen}
                  onClick={toggleFoldedOpen}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') toggleFoldedOpen();
                  }}
                >
                  <span className="chev"><IconChevR /></span>
                  <span className="lbl">{t('context.menu.03')}</span>
                  <span className="n">{foldedCount}</span>
                </div>
                <div
                  className="fold-body"
                  data-knowe-conv-entry="fold-body"
                  aria-hidden={!foldedOpen}
                  style={foldedOpen ? { maxHeight: 'none' } : undefined}
                >
                  {folded.map((p) => (
                    <ConvListItem
                      key={p.id}
                      id={p.id}
                      name={p.name}
                      draft={p.draft}
                      workingAgents={p.workingAgents}
                      active={p.id === activeId}
                      menuFolded
                      menuMuted={!!mutedProjects[p.id]}
                      data-knowe-menu-hidden={!foldedOpen}
                      onOpen={() => switchProject(p.id)}
                    />
                  ))}
                </div>
              </>
            )}
          </>
        )}
          </>
        )}
      </div>

      <NewProjectModal open={modalOpen} onClose={() => setModalOpen(false)} />

      {/* [v0.5 #14] 左栏右边缘这条线可以拖——拖窄了自动进紧凑模式（只剩头像） */}
      <ResizeHandle />
    </aside>
  );
};

// ═══════════════════════════════════════════════════════════════

interface ConvListItemProps {
  /** [v0.4] 图片头像（知知用 zinnia.png） */
  avatarUrl?: string;
  /** [v0.4] 固定副标题（知知没有聊天记录时也该说点什么） */
  subtitle?: string;
  id: string;
  name: string;
  /** [v0.7 #1] 这个会话没发出去的字。非空 → 预览位显示红色「[草稿]」 */
  draft?: string;
  /** [v0.8b #11] 置顶（深底色）。知知永远置顶。 */
  pinned?: boolean;
  /** [v0.44.8] 菜单置顶状态（不同于知知/私聊面板的硬置顶）。 */
  menuPinned?: boolean;
  /** [v0.44.8] 菜单免打扰状态。 */
  menuMuted?: boolean;
  /** [v0.44.8] 菜单折叠状态。 */
  menuFolded?: boolean;
  /** [v0.44.8] 由菜单折叠态控制卡片显隐。 */
  'data-knowe-menu-hidden'?: boolean;
  /** 当前工作的成员；项目列表据此显示蓝色状态预览。 */
  workingAgents?: WorkingAgentSummary[];
  active: boolean;
  onOpen: () => void;
}

/** 草稿预览只露前 20 个字——列表是一行的事，不是文章 */
const DRAFT_PREVIEW_LEN = 20;
const WORKING_BLUE = 'var(--accent, #4f7cff)';

function workingPreview(agents: WorkingAgentSummary[]): string {
  const roles = Array.from(new Set(agents.map((a) => roleLabel(a.role) || a.displayName).filter(Boolean)));
  const shown = roles.slice(0, 3).join('/');
  const more = roles.length > 3 ? i18n.t('conv.list.roleMore', { n: roles.length }) : '';
  return i18n.t('conv.list.working', { shown, more });
}

export const ConvListItem: React.FC<ConvListItemProps> = ({
  id, name, active, onOpen, avatarUrl, subtitle, draft, pinned = false,
  menuPinned = false, menuMuted = false, menuFolded = false,
  'data-knowe-menu-hidden': menuHidden = false,
  workingAgents = [],
}) => {
  const { t } = useCachedT();
  // [v0.8b #10] 私聊没有「人数」，也没有「待确认」——那是群聊才有的东西。
  const priv = isPrivateChat(id);
  // 会话项的预览/时间需要后端提供；当前契约没有这些字段。
  // 铁律「不编造数据」：预览位显示成员数占位，时间位留空。
  // [v0.9b] 归档的人不算「在队的人数」—— 他不再接活了。
  const memberCount = useKnoweStore(
    (s) => (s.convs[id]?.members ?? []).filter((m) => m.status !== 'removed').length,
  );

  // [v0.8d #5] 未读数。**知知也算**——她也是左栏里的一个会话，她说了话你也该看见。
  const unread = useKnoweStore((s) => s.convs[id]?.unread ?? 0);

  // [v0.13 卡片] 该项目目录失效/暂缓 → 预览位亮红字「未处理事项」（需求 3）。
  const dirPending = useDirectoryPending(id);

  /*
   * [v0.5b #6] 群头像做成宫格 —— 一个群里有几个人，头像上就看得出几个人。
   *
   * 项目经理排第一个（左上）。成员从 store 拿，所以人一进来、头像就跟着变，
   * 不用另外通知谁。
   */
  // [v1.0.24.6-P1a] shallow：members 数组未变（引用不变）→ GridMember 字段全等
  // → 不重渲染。原实现每次 convs 变化都重建新数组，流式事件每秒拉着重渲染。
  const gridMembers = useKnoweStore((s): GridMember[] => {
    const ms = s.convs[id]?.members ?? [];
    const rank = (m: { id: string }): number => (m.id === 'coordinator' ? 0 : 1);
    return [...ms]
      // [v0.9b] 归档的人不上宫格。
      //   （他仍然留在 members 里——历史气泡要靠它认脸；只是不在"现在这个群里有谁"的画面上。）
      .filter((m) => m.status !== 'removed')
      .sort((a, b) => rank(a) - rank(b))
      .map((m) => ({
        id: m.id,
        glyph: m.display.glyph,
        pal: m.display.pal,
        avatarUrl: m.display.avatarUrl,
      }));
  }, shallow);
  const pending = useKnoweStore(
    (s) => (s.convs[id]?.items ?? []).filter(
      (it) => it.kind === 'approval' && it.state === 'pending',
    ).length,
  );
  /*
   * [v0.23 问题四] 左栏预览：**跳过空文本的条目**。
   *
   *   现象：Worker 说了一句话，项目经理用 NOTHING_TO_ADD 沉默 —— 左栏却显示「还没有消息」。
   *
   *   老写法是「倒着找最后一条 user/agent/system，找到就 return it.text」。
   *   一旦那条的 text 是空串（项目经理的沉默、被 dedupe 掉的收尾、被打断的空回合），
   *   它就 return '' —— 然后 `lastText || subtitle || '还没有消息'` 一路兜到最后那句。
   *   **Worker 那条真消息明明就在前面一格，被跳过了。**
   *
   *   ★ 空消息是**信号**，不是**消息**：它的用处是让成员收回 idle、让流式气泡落定
   *     （见 state.ts 的 message 分支），不是拿来给人看的。所以预览要找的是
   *     「最后一条**有字**的」，不是「最后一条」。
   *
   *   （state.ts 已经挡住了空气泡进 items —— 我把真实事件序列灌进 reducer 跑过，
   *     确实不进。但预览这一层不该依赖上游永远不出错：这里加一道 `.trim()` 判断，
   *     成本是一个字符，换来的是这类 bug 再也不会从任何上游漏到左栏。）
   */
  const lastText = useKnoweStore((s) => {
    const items = s.convs[id]?.items ?? [];
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i];
      if (!it) continue;
      if (it.kind === 'user' || it.kind === 'agent' || it.kind === 'system') {
        if (it.text && it.text.trim()) return it.text;
        continue;                      // 空的 → 接着往前找，别停在这儿
      }
    }
    return '';
  });

  const draftText = (draft ?? '').trim();
  const workingText = workingAgents.length > 0 ? workingPreview(workingAgents) : '';
  const draftPreview = draftText.length > DRAFT_PREVIEW_LEN
    ? draftText.slice(0, DRAFT_PREVIEW_LEN) + '…'
    : draftText;

  return (
    <div
      className={'citem'
        + (active ? ' active' : '')
        + (pinned ? ' pinned' : '')          /* [v0.8b #11] */
        + (menuPinned ? ' pinned' : '')
        + (menuMuted ? ' knowe-conv-muted' : '')
        + (menuFolded ? ' knowe-conv-folded' : '')
        + (draftText ? ' has-draft' : '')
        + (workingText ? ' working' : '')
        + (unread > 0 ? ' has-unread' : '')} /* [v0.8d #5] */
      data-conv={id}
      data-knowe-menu-hidden={menuHidden || undefined}
      aria-hidden={menuHidden || undefined}
      tabIndex={0}
      role="button"
      aria-label={priv ? `${t('context.menu.05')} ${name}` : `${t('common.14')} ${name}`}
      aria-current={active}
      onClick={onOpen}
      onContextMenu={id === PLATFORM_PROJECT_ID ? undefined : (e) => {
        e.preventDefault();
        openConversationMenu(id, e.clientX, e.clientY);
      }}
      onKeyDown={(e) => { if (e.key === 'Enter') onOpen(); }}
    >
      <div
        className="cav"
        onContextMenu={id === PLATFORM_PROJECT_ID ? (e) => {
          e.preventDefault();
          e.stopPropagation();
          openAgentMenu(PLATFORM_PROJECT_ID, 'zinnia', e.clientX, e.clientY);
        } : undefined}
      >
        {avatarUrl || gridMembers.length === 0 ? (
          // 知知（固定头像）、以及还没有任何成员的空群 → 单个头像，不摆宫格
          <Avatar glyph={glyphOf(name)} pal={palOf(id)} size={44} src={avatarUrl} />
        ) : (
          <AvatarGrid members={gridMembers} title={name} />
        )}
      </div>
      <div className="cbody">
        <div className="cname">
          <span>{name}</span>
          {menuMuted && (
            <span className="bell" aria-label={t('context.menu.04')}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M8.7 3.5A6 6 0 0 1 18 8.5c0 3 .7 4.9 1.5 6M17.5 17.5H5s2-1.5 2-9M10.3 21a2 2 0 0 0 3.4 0" />
                <path d="M3 3l18 18" />
              </svg>
            </span>
          )}
          {menuPinned && (
            <span className="pin-ic" aria-label={t('context.menu.07')}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 17v5" /><path d="M9 10.8V4h6v6.8l2 2.2H7z" />
              </svg>
            </span>
          )}
        </div>
        {/*
          [v0.7 #1] 有草稿 → 预览位让位给草稿：红色的「[草稿]」+ 前 20 字。
          （微信的规矩：没发出去的东西比已经发生过的事更要紧。）
          [v0.13 卡片] 但「目录失效/暂缓」比草稿更要紧——项目整个被卡住了，什么也做不了，
          所以它排在最前，红字「[未处理事项]」提醒用户点进去重新选目录。
        */}
        {dirPending ? (
          <div className="cprev">
            <span
              className="pending-tag"
              style={{ color: '#e5484d', fontWeight: 600, marginRight: 4 }}
            >
              {t('conv.list.pendingBadge')}
            </span>
            <span style={{ opacity: 0.7 }}>{t('conv.list.10')}</span>
          </div>
        ) : workingText ? (
          <div
            className="cprev working-preview"
            style={{ color: WORKING_BLUE, fontWeight: 600 }}
            title={workingAgents.map((a) => `${a.displayName}（${roleLabel(a.role)}）`).join('、')}
          >
            <span
              aria-hidden="true"
              style={{
                width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
                marginRight: 6, background: 'currentColor', boxShadow: '0 0 0 3px rgba(79,124,255,.14)',
              }}
            />
            <span>{workingText}</span>
          </div>
        ) : draftText ? (
          <div className="cprev">
            <span className="draft-tag">{t('conv.list.03')}</span>
            <span className="draft-body">{draftPreview}</span>
          </div>
        ) : (
          <div className="cprev">{lastText || subtitle || t('conv.list.08')}</div>
        )}
      </div>
      <div className="cmeta">
        <div className="ctime tnum" />
        {/*
          [v0.8d #5] 未读排在最前面：它是**唯一一件需要你现在就知道的事**。
          人数、待确认都是「这个群的状态」，未读是「有人在等你」。

          [v0.8b #10] 私聊（知知）不显示人数和「待确认」——
          在跟一个人的对话框上写「1 人」，跟微信在跟妈妈的聊天里写「本群 1 人」一样滑稽。
          但**未读她照样有**：她说了话，你也该看见。
        */}
        {unread > 0 ? (
          <div className="unread-pill tnum" aria-label={t('conv.list.unreadAria', { n: unread })}>
            {unread > 99 ? '99+' : unread}
          </div>
        ) : priv ? null : pending > 0 ? (
          <div className="await"><span className="dot" /><span className="tx">{t('conv.list.04')}</span></div>
        ) : memberCount > 0 ? (
          <div className="member-count tnum">{t('common.peopleCount', { n: memberCount })}</div>
        ) : null}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════
// [v0.37] 群内私聊面板里的一行成员（缩进、个人头像、可点进私聊）
// ═══════════════════════════════════════════════════════════════

const DmMemberRow: React.FC<{
  member: Member;
  active: boolean;
  onOpen: () => void;
}> = ({ member, active, onOpen }) => {
  const { t } = useCachedT();
  const d = member.display;
  const working = member.state === 'busy';
  return (
    <button
      type="button"
      className={'dm-member' + (active ? ' active' : '') + (working ? ' working' : '')}
      role="listitem"
      aria-current={active}
      aria-label={`${t('context.menu.05')} ${d.name}`}
      title={`${t('context.menu.05')} ${d.name}`}
      // 单击进私聊；双击也进（双击=单击的超集，避免用户「点重了」反而没反应）。
      onClick={onOpen}
      onDoubleClick={onOpen}
    >
      <span className="dm-member-av">
        <Avatar glyph={d.glyph} pal={d.pal} size={28} title={d.name} src={d.avatarUrl} />
        {working && <span className="dm-busy-dot" aria-hidden="true" />}
      </span>
      <span className="dm-member-body">
        <span className="dm-member-name">{d.name}</span>
        {d.role && !d.name.includes(d.role) && (
          <span className="dm-member-role">{roleLabel(d.role)}</span>
        )}
      </span>
    </button>
  );
};

export default ConvList;
