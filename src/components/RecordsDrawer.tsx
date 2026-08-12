// src/components/RecordsDrawer.tsx
// [v0.38] 聊天记录抽屉（主组件）。
//
// 在 ChatStream 里当 useRecordsStore.open === true 时渲染，morph 展开覆盖整张 .chat-card。
//   <RecordsDrawer projectId={sessionId} isGroup={bool} />
//   projectId = 当前窗口的 session id：群聊=群 project_id，私聊=dm:{group}:{agent}（后端认得）。
//
// 发送者的脸/名字复用花名册（selectActiveMembers）+ 项目自带的 <Avatar>，与聊天区一致。

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom'; // [v1.0.24.1] 右键菜单 portal 到 body，脱离抽屉 transform
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { useKnoweStore } from '../store/store';
import { selectActiveMembers } from '../store/selectors';
import type { Member } from '../store/state';
import { memberNameLabel } from '../shared/roleLabel';
import { Avatar } from './Avatar';
import { IconSearchSm, IconX } from './icons';
import {
  useRecordsStore, filterBySearch, groupFileHistory, RECORDS_TABS,
  type FileGroupMode, type FileHistoryGroup,
  type HistoryItem, type HandoffItem, type RecordsCategory,
} from '../store/records';
import DateCalendar from './DateCalendar';
import { formatFullTimestamp, truncate } from '../utils/messageTime';
import { extractUrls } from '../utils/links';
import InlineMarkdown from './InlineMarkdown';   // [v0.38.1 #7] 摘要内联 Markdown
import { Markdown } from './markdown';           // [v0.38.3 #4] handoff 预览用块级 Markdown
import { FileCardList } from './FileCard';        // [v0.38.1 #9] 复用聊天区文件卡片 + 点击预览

export interface RecordsDrawerProps {
  projectId: string;
  /** 项目创建日期（YYYY-MM-DD），传给日历做下限。可选。 */
  projectCreatedDate?: string | null;
  /** [v0.38.3 #4] 是否群聊——群聊才显示「报告/交接」标签。 */
  isGroup?: boolean;
}

interface Face { name: string; glyph: string; pal: string; avatarUrl?: string; isUser: boolean; }

function useFaceResolver(): (item: HistoryItem) => Face {
  const members = useKnoweStore(selectActiveMembers) as Member[];
  return useMemo(() => {
    const byId = new Map(members.map((m) => [m.id, m]));
    return (item: HistoryItem): Face => {
      const isUser = item.type === 'user_echo' || !item.agent_id;
      if (isUser) return { name: '我', glyph: '我', pal: 'av-n', isUser: true };
      const m = byId.get(item.agent_id);
      if (m) {
        return {
          name: memberNameLabel(m.id, m.display.name), glyph: m.display.glyph, pal: m.display.pal,
          avatarUrl: m.display.avatarUrl, isUser: false,
        };
      }
      return { name: item.agent_id, glyph: item.agent_id.charAt(0) || '?', pal: 'av-d', isUser: false };
    };
  }, [members]);
}

function Tags({ item }: { item: HistoryItem }): React.ReactElement | null {
  const { t } = useTranslation();
  const tags: string[] = [];
  if (item.has_files) tags.push(t('common.02'));
  if (item.has_images) tags.push(t('common.01'));
  if (item.has_videos) tags.push(t('common.11'));
  if (item.has_links) tags.push(t('common.13'));
  if (!tags.length) return null;
  return (
    <span className="rec-tags">
      {tags.map((tag) => <span key={tag} className="rec-tag">{tag}</span>)}
    </span>
  );
}

interface CtxMenu { x: number; y: number; seq: number; }

/**
 * [v1.0.24.1] 右键菜单：portal 到 document.body + 视口边界钳制。
 * 根因：.drawer-wrap 动画 fill:both 永久残留 transform（matrix(1,0,0,1,0,0)），
 * position:fixed 的包含块从视口变成抽屉 → clientX/Y 坐标整体偏移抽屉的位移，
 * 鼠标靠右时菜单直接飞出窗口。portal 到 body 后脱离 transform 祖先，恢复跟鼠标。
 * 钳制：菜单右/下边缘超出视口时向内收，保证永远可见。
 */
function RecCtxMenu({ x, y, label, onPick }: {
  x: number; y: number; label: string; onPick: () => void;
}): React.ReactPortal {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const m = 8;
    setPos({
      x: Math.max(m, Math.min(x, window.innerWidth - r.width - m)),
      y: Math.max(m, Math.min(y, window.innerHeight - r.height - m)),
    });
  }, [x, y]);
  return createPortal(
    <div ref={ref} className="rec-ctxmenu" style={{ left: pos.x, top: pos.y }} onMouseDown={(e) => e.stopPropagation()}>
      <button type="button" className="rec-ctxmenu-item" onClick={onPick}>{label}</button>
    </div>,
    document.body,
  );
}

function formatFileDateLabel(dateKey: string | null | undefined): string {
  if (!dateKey) return i18n.t('records.drawer.17');
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateKey);
  if (!match) return dateKey;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'short',
  }).format(date);
}

function formatFileClock(ts: number | null): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return i18n.t('records.drawer.18');
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return i18n.t('records.drawer.18');
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export default function RecordsDrawer({
  projectId, projectCreatedDate = null, isGroup = false,
}: RecordsDrawerProps): React.ReactElement {
  const { t } = useTranslation();
  const open = useRecordsStore((s) => s.open);
  const category = useRecordsStore((s) => s.category);
  const searchQuery = useRecordsStore((s) => s.searchQuery);
  const selectedDate = useRecordsStore((s) => s.selectedDate);
  const loading = useRecordsStore((s) => s.loading);
  const error = useRecordsStore((s) => s.error);
  const items = useRecordsStore((s) => s.items);
  const total = useRecordsStore((s) => s.total);
  const handoffs = useRecordsStore((s) => s.handoffs);
  const handoffsLoading = useRecordsStore((s) => s.handoffsLoading);

  const closeDrawer = useRecordsStore((s) => s.closeDrawer);
  const setCategory = useRecordsStore((s) => s.setCategory);
  const setSearchQuery = useRecordsStore((s) => s.setSearchQuery);
  const setSelectedDate = useRecordsStore((s) => s.setSelectedDate);
  const requestJump = useRecordsStore((s) => s.requestJump);
  const load = useRecordsStore((s) => s.load);
  const loadMore = useRecordsStore((s) => s.loadMore);
  const loadHandoffs = useRecordsStore((s) => s.loadHandoffs);

  const faceOf = useFaceResolver();
  const visible = useMemo(() => filterBySearch(items, searchQuery), [items, searchQuery]);
  const hasMore = items.length < total;

  // [v1.0.24.1] 关闭动画：先置 closing（400ms 滑出）再卸载——与 AddAgentsPopover 同款模式
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const handleClose = useCallback((): void => {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    // ★ 卸载延迟必须与 CSS .drawer-wrap.closing 的 animation-duration 同步（当前 400ms）——
    //   改 CSS 时长时必须同步改这里，否则动画播一半就被卸载（曾踩坑：CSS 改 400 后此处仍 200）。
    window.setTimeout(() => closeDrawer(), 400);
  }, [closeDrawer]);

  // [v0.38.3 #3] 右键菜单 & [#4] handoff 预览的本地态
  const [menu, setMenu] = useState<CtxMenu | null>(null);
  const [preview, setPreview] = useState<HandoffItem | null>(null);
  // [v1.0.18.2 E-2] 文件 tab 的客户端分组与快速定位，不增加 /history 参数。
  // [v1.0.24.1] 分组推广到全部/图片/视频/链接 tab：非文件 tab 不过滤无文件消息（includeAll）。
  const [fileGroupMode, setFileGroupMode] = useState<FileGroupMode>('sender');
  const [fileGroupKey, setFileGroupKey] = useState('all');
  const fileGroups = useMemo(
    () => groupFileHistory(visible, fileGroupMode, category !== 'files'),
    [visible, fileGroupMode, category],
  );
  const locatedFileGroups = fileGroupKey === 'all'
    ? fileGroups
    : fileGroups.filter((group) => group.key === fileGroupKey);

  // 打开 / 切分类 / 切日期 / 切 DM → 加载（date 需先选日期；handoff 走单独端点）
  useEffect(() => {
    if (!open || category === 'handoff') return;
    if (category === 'date' && !selectedDate) return;
    load(projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, category, selectedDate, projectId]);

  // [v0.38.3 #4] handoff 分类 → 拉取报告/交接
  useEffect(() => {
    if (!open || category !== 'handoff') return;
    loadHandoffs(projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, category, projectId]);

  // 群聊切私聊等场景：若当前停在 handoff 但已不是群聊 → 退回全部
  useEffect(() => {
    if (!isGroup && category === 'handoff') setCategory('all');
  }, [isGroup, category, setCategory]);

  useEffect(() => {
    setFileGroupKey('all');
  }, [fileGroupMode, projectId]);

  useEffect(() => {
    if (fileGroupKey !== 'all' && !fileGroups.some((group) => group.key === fileGroupKey)) {
      setFileGroupKey('all');
    }
  }, [fileGroupKey, fileGroups]);

  // Esc 关闭（有右键菜单/预览时先关它们）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return;
      if (menu) { setMenu(null); return; }
      if (preview) { setPreview(null); return; }
      handleClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, handleClose, menu, preview]);

  // 点任意处关右键菜单
  useEffect(() => {
    if (!menu) return;
    const onDown = (): void => setMenu(null);
    window.addEventListener('mousedown', onDown);
    window.addEventListener('scroll', onDown, true);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('scroll', onDown, true);
    };
  }, [menu]);

  const tabs: { key: RecordsCategory; label: string }[] = isGroup
    ? [...RECORDS_TABS.map((r) => ({ ...r, label: t(r.label) })), { key: 'handoff', label: t('records.drawer.10') }]
    : RECORDS_TABS.map((r) => ({ ...r, label: t(r.label) }));

  const pager = (!searchQuery.trim() && hasMore) ? (
    <div className="dr-pagination">
      <button type="button" className="dr-load-more" disabled={loading}
        onClick={() => loadMore(projectId)}>
        {loading ? t('records.drawer.07') : t('records.drawer.08')}
      </button>
    </div>
  ) : null;

  // [v0.38.3 #3] 右键 → 记住位置与目标 seq
  const onRowContext = (seq: number | undefined) => (e: React.MouseEvent): void => {
    if (seq == null) return;
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY, seq });
  };

  const jumpTo = (seq: number): void => {
    setMenu(null);
    closeDrawer();          // 抽屉丝滑消失（drawer-wrap 卸载）
    requestJump(seq);       // ChatStream 消费：滚动 + 高亮
  };

  const timelineRow = (it: HistoryItem): React.ReactElement => {
    const f = faceOf(it);
    return (
      <div className="rec-row" key={it.seq} onContextMenu={onRowContext(it.seq)}>
        <Avatar glyph={f.glyph} pal={f.pal} size={28} src={f.avatarUrl} title={f.name} />
        <div className="rec-main">
          <div className="rec-meta">
            <span className="rec-name">{f.name}</span>
            <span className="rec-time">{formatFullTimestamp(it.ts)}</span>
          </div>
          {/* [v0.38.1 #7] 摘要走内联 Markdown：`**加粗**` 显示为加粗，不再露出星号。 */}
          <div className="rec-bubble"><InlineMarkdown text={truncate(it.content, 80)} /></div>
          <Tags item={it} />
        </div>
      </div>
    );
  };

  const fileGroupLabel = (group: FileHistoryGroup): string => {
    if (group.mode === 'date') return formatFileDateLabel(group.dateKey);
    const representative = group.items[0];
    return representative ? faceOf(representative).name : (group.senderId || t('records.drawer.25'));
  };

  // 文件卡继续复用聊天区 FileCardList；这里只补互补维度的元数据和消息出处菜单。
  const fileMessageGroup = (item: HistoryItem): React.ReactElement | null => {
    if (!item.files || item.files.length === 0) return null;
    const face = faceOf(item);
    const primary = fileGroupMode === 'sender' ? formatFullTimestamp(item.ts) : face.name;
    const secondary = fileGroupMode === 'sender' ? null : formatFileClock(item.ts);
    return (
      <div className="rec-file-message" key={item.seq} onContextMenu={onRowContext(item.seq)}>
        <div className="rec-file-message-meta">
          <span>{primary}</span>
          {secondary && <><span className="rec-dot">·</span><span>{secondary}</span></>}
        </div>
        <FileCardList files={item.files} projectId={projectId} />
      </div>
    );
  };

  const fileHistoryGroup = (group: FileHistoryGroup): React.ReactElement => {
    const representative = group.items[0];
    const face = representative ? faceOf(representative) : null;
    return (
      <section className="rec-file-section" key={group.key} data-file-group={group.key}>
        <header className="rec-file-section-head">
          {group.mode === 'sender' && face && (
            <Avatar glyph={face.glyph} pal={face.pal} size={28} src={face.avatarUrl} title={face.name} />
          )}
          <div className="rec-file-section-title">
            <strong>{fileGroupLabel(group)}</strong>
            <span>{t('records.drawer.groupSummary', { n: group.messageCount, m: group.fileCount })}</span>
          </div>
        </header>
        <div className="rec-file-section-body">{group.items.map(fileMessageGroup)}</div>
      </section>
    );
  };

  const linkRows = (it: HistoryItem): React.ReactElement[] => {
    const f = faceOf(it);
    return extractUrls(it.content).map((url) => (
      <div className="rec-link" key={`${it.seq}-${url}`}>
        <span className="rec-link-ic" aria-hidden="true">🔗</span>
        <div className="rec-link-body">
          <a className="rec-link-url" href={url} target="_blank" rel="noopener noreferrer" title={url}>
            {truncate(url, 60)}
          </a>
          <div className="rec-link-meta">
            <span>{f.name}</span>
            <span className="rec-dot">·</span>
            <span>{formatFullTimestamp(it.ts)}</span>
          </div>
        </div>
      </div>
    ));
  };

  const empty = (text: string): React.ReactElement => <div className="dr-empty">{text}</div>;

  // [v1.0.24.1] 分组工具栏：文件/全部/图片/视频/链接 共用（按发送人/按日期 + 定位器）。
  const groupToolbar = (): React.ReactElement => (
    <div className="rec-files-toolbar">
      <div className="rec-file-mode" role="group" aria-label={t('records.drawer.16')}>
        <button
          type="button" aria-pressed={fileGroupMode === 'sender'}
          className={fileGroupMode === 'sender' ? 'active' : ''}
          onClick={() => setFileGroupMode('sender')}
        >{t('records.drawer.11')}</button>
        <button
          type="button" aria-pressed={fileGroupMode === 'date'}
          className={fileGroupMode === 'date' ? 'active' : ''}
          onClick={() => setFileGroupMode('date')}
        >{t('records.drawer.12')}</button>
      </div>
      <label className="rec-file-locator">
        <span>{t('records.drawer.09')}</span>
        <select value={fileGroupKey} onChange={(event) => setFileGroupKey(event.target.value)}>
          <option value="all">{fileGroupMode === 'sender' ? t('records.drawer.04') : t('records.drawer.05')}</option>
          {fileGroups.map((group) => (
            <option value={group.key} key={group.key}>{fileGroupLabel(group)}</option>
          ))}
        </select>
      </label>
    </div>
  );

  // [v1.0.24.1] 非文件 tab 的分组 section：组内按当前分类渲染行（全部=时间线 / 图片视频=宫格 / 链接=链接行）。
  const groupedBody = (group: FileHistoryGroup): React.ReactNode => {
    if (category === 'images' || category === 'videos') {
      return (
        <div className="dr-grid">
          {group.items.map((it) => (
            <div className="dr-thumb" key={it.seq} title={truncate(it.content, 40)}>
              {category === 'images' ? '🖼' : '🎬'}
            </div>
          ))}
        </div>
      );
    }
    if (category === 'links') {
      return <div className="rec-list">{group.items.flatMap(linkRows)}</div>;
    }
    return <div className="rec-list">{group.items.map(timelineRow)}</div>;
  };

  const groupedSection = (group: FileHistoryGroup): React.ReactElement => {
    const representative = group.items[0];
    const face = representative ? faceOf(representative) : null;
    return (
      <section className="rec-file-section" key={group.key} data-file-group={group.key}>
        <header className="rec-file-section-head">
          {group.mode === 'sender' && face && (
            <Avatar glyph={face.glyph} pal={face.pal} size={28} src={face.avatarUrl} title={face.name} />
          )}
          <div className="rec-file-section-title">
            <strong>{fileGroupLabel(group)}</strong>
            <span>{t('records.drawer.groupSummaryMsg', { n: group.messageCount })}</span>
          </div>
        </header>
        <div className="rec-file-section-body">{groupedBody(group)}</div>
      </section>
    );
  };

  // 分组 tab（全部/图片/视频/链接）的统一渲染：工具栏 + 分组 sections。
  const renderGroupedTab = (emptyText: string): React.ReactNode => {
    if (!visible.length) return empty(emptyText);
    return (
      <div className="rec-files">
        {groupToolbar()}
        <div className="rec-file-sections">{locatedFileGroups.map(groupedSection)}</div>
        {pager}
      </div>
    );
  };

  const renderBody = (): React.ReactNode => {
    if (error && category !== 'handoff') {
      return (
        <div className="dr-empty dr-error">
          {t('records.drawer.loadFailed', { error })}
          <div><button type="button" className="dr-retry" onClick={() => load(projectId)}>{t('common.03')}</button></div>
        </div>
      );
    }

    if (category === 'handoff') {
      if (handoffsLoading && !handoffs.length) return empty(t('records.drawer.07'));
      if (!handoffs.length) return empty(t('records.drawer.20'));
      return (
        <div className="rec-ho-list">
          {handoffs.map((h) => (
            <button type="button" className="rec-ho-card" key={String(h.id)} onClick={() => setPreview(h)}>
              <span className="rec-ho-ic" aria-hidden="true">📋</span>
              <span className="rec-ho-main">
                <span className="rec-ho-title">{h.title || t('records.drawer.03')}</span>
                <span className="rec-ho-time">{formatFullTimestamp(h.ts)}</span>
              </span>
            </button>
          ))}
        </div>
      );
    }

    if (category === 'date') {
      return (
        <div className="dr-date">
          <DateCalendar
            projectId={projectId} selectedDate={selectedDate}
            onSelect={setSelectedDate} minDate={projectCreatedDate}
          />
          <div className="dr-date-list">
            {!selectedDate ? empty(t('records.drawer.27'))
              : (loading && !visible.length) ? empty(t('records.drawer.07'))
                : !visible.length ? empty(t('records.drawer.29'))
                  : <>{visible.map(timelineRow)}{pager}</>}
          </div>
        </div>
      );
    }

    if (loading && !visible.length) return empty(t('records.drawer.07'));

    if (category === 'images' || category === 'videos') {
      if (!visible.length) {
        return (
          <div className="dr-empty dr-empty-ph">
            <span className="dr-empty-ic" aria-hidden="true">{category === 'images' ? '🖼' : '🎬'}</span>
            {category === 'images' ? t('records.drawer.19') : t('records.drawer.23')}
          </div>
        );
      }
      // [v1.0.24.1] 与文件 tab 同款：按发送人/按日期分组 + 定位器
      return renderGroupedTab(category === 'images' ? t('records.drawer.19') : t('records.drawer.23'));
    }

    if (category === 'files') {
      if (!fileGroups.length) return empty(searchQuery.trim() ? t('records.drawer.26') : t('records.drawer.21'));
      return (
        <div className="rec-files">
          {groupToolbar()}
          <div className="rec-file-sections">{locatedFileGroups.map(fileHistoryGroup)}</div>
          {pager}
        </div>
      );
    }

    if (category === 'links') {
      // [v1.0.24.1] 与文件 tab 同款：按发送人/按日期分组 + 定位器
      return renderGroupedTab(t('records.drawer.24'));
    }

    // all —— [v1.0.24.1] 同样支持分组 + 定位器
    return renderGroupedTab(t('records.drawer.22'));
  };

  return (
    <div className={`drawer-wrap${open ? ' open' : ''}${closing ? ' closing' : ''}`}>
      <div className="drawer" role="dialog" aria-label={t('common.21')}>
        <div className="dr-head">
          <div className="dr-title">{t('common.21')}</div>
          <button type="button" className="dr-close" onClick={handleClose} aria-label={t('records.drawer.06')}>
            <IconX />
          </button>
        </div>

        <div className="search-wrap dr-search">
          <div className="search">
            <IconSearchSm />
            <input
              type="text" placeholder={category === 'files' ? t('records.drawer.13') : t('records.drawer.15')} value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)} aria-label={category === 'files' ? t('records.drawer.14') : t('records.drawer.15')}
            />
          </div>
        </div>

        <div className="dr-tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.key} type="button" role="tab" aria-selected={category === t.key}
              className={`dr-tab${category === t.key ? ' active' : ''}`}
              onClick={() => { setCategory(t.key); setFileGroupKey('all'); }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="dr-body">{renderBody()}</div>

        {/* [v0.38.3 #4] handoff 预览：盖在 body 上，返回可关 */}
        {preview && (
          <div className="rec-ho-preview">
            <div className="rec-ho-pv-head">
              <button type="button" className="rec-ho-back" onClick={() => setPreview(null)} aria-label={t('records.drawer.28')}>{t('records.drawer.02')}</button>
              <span className="rec-ho-pv-title">{preview.title || t('records.drawer.03')}</span>
            </div>
            <div className="rec-ho-pv-body">
              {preview.instruction && (
                <section className="rec-ho-sec">
                  <div className="rec-ho-sec-label">{t('knowledge.view.03')}</div>
                  <Markdown text={preview.instruction} />
                </section>
              )}
              {preview.report && (
                <section className="rec-ho-sec">
                  <div className="rec-ho-sec-label">{t('common.08')}</div>
                  <Markdown text={preview.report} />
                </section>
              )}
              {!preview.instruction && !preview.report && <div className="dr-empty">{t('records.drawer.30')}</div>}
            </div>
          </div>
        )}
      </div>

      {/* [v0.38.3 #3] 右键菜单：跳转到消息出处（[v1.0.24.1] portal 到 body + 边界钳制） */}
      {menu && (
        <RecCtxMenu x={menu.x} y={menu.y} label={t('records.drawer.jumpToSource')} onPick={() => jumpTo(menu.seq)} />
      )}
    </div>
  );
}
