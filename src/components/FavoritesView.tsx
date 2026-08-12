/**
 * FavoritesView.tsx — [v0.40.0] 收藏界面（README §四）
 *
 * 与 ContactsView 同款 SidePanel + Stage 骨架，挂 .view-alt（activeView==='favorites'）。
 * DOM 与视觉**逐块照抄** reference 的 renderFavorites（3060–3042 行）+ ContentCard：
 *   side  : .side > .side-head(收藏) + .search-wrap + 「＋ 新建笔记」btn-primary(宽 100%，
 *           外层 padding 0 16px 8px) + .side-scroll(.navrow 分类×9 + .sec-head 标签 + 标签行)
 *   stage : .stage > .stage-card > .stage-head(.stage-h1 当前分类 + .stage-sub「N 项 · 来自
 *           消息、报告与笔记」) + .stage-scroll(.card × N ／ .empty2 空态)
 *   card  : .card > (.card-top > .card-title + .card-thumb 类型图标)
 *           + .card-body 摘要 + (.card-meta > 18px 小头像 + .lk 来源(可点跳回)
 *           + .sep · + 类型 + 日期(margin-left:auto))
 *
 * 交互（README §4.3–4.6）：
 *   · 搜索实时过滤（标题或摘要，纯前端 filter，无防抖需求——条目量级很小）
 *   · 分类/标签筛选，选中行 .active 高亮，计数实时
 *   · 卡片右键菜单：打开 / 转发 / 编辑标签 / 移除收藏(danger)
 *   · 打开 & 来源名点击：切回聊天 → 定位原消息 → .flash 高亮闪烁
 *     （flashItem 逻辑照抄 reference 2265 行；.mgroup.flash CSS 既有）
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { useKnoweStore } from '../store/store';
import {
  useFavoritesStore, favDateLabel, RECENT_WINDOW_MS, type FavEntry, type FavType,
} from '../store/favorites';
import type { ForwardItem } from '../store/state';
import {
  openMenu, toast, openForwardPicker, openTagEditor, openInputModal, openNoteComposer, type MenuEntry,
} from './ContextMenu';
import { Markdown } from './markdown';
import FileCard from './FileCard';
import {
  IconStar, IconChats, IconLink, IconImage, IconEdit, IconFolder,
  IconBook, IconReport, IconSpark, IconTag, IconForward, IconTrash, IconX,
} from './icons';

/** 16px 消息图标（reference ICON.msg；IconChats 是 19px 导航尺寸，塞不进 .mic 的 16px 盒）。 */
const Msg16: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

/** 分区头的向下小箭头（reference ICON.chevD；icons.tsx 尚无 16px chevron-down，就地画）。 */
const ChevD: React.FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="m6 9 6 6 6-6" />
  </svg>
);

// ── 分类表（名称/顺序/图标照 reference FAV_CATS；计数换成真数据实时算） ──

type CatId =
  | '全部收藏' | '最近使用' | '链接' | '图片与视频' | '笔记'
  | '文件' | '聊天记录' | '报告与产出' | '知识卡片';

const CATS: { id: CatId; icon: React.ReactNode }[] = [
  { id: '全部收藏', icon: <IconStar /> },
  { id: '最近使用', icon: <IconChats /> },
  { id: '链接', icon: <IconLink /> },
  { id: '图片与视频', icon: <IconImage /> },
  { id: '笔记', icon: <IconEdit /> },
  { id: '文件', icon: <IconFolder /> },
  { id: '聊天记录', icon: <IconBook /> },
  { id: '报告与产出', icon: <IconReport /> },
  { id: '知识卡片', icon: <IconSpark /> },
];

function inCat(e: FavEntry, cat: CatId): boolean {
  switch (cat) {
    case '全部收藏': return true;
    case '最近使用': return Date.now() - e.lastUsedAt < RECENT_WINDOW_MS;
    case '图片与视频': return e.type === '图片' || e.type === '视频';
    case '报告与产出': return e.type === '报告';
    default: return e.type === cat;
  }
}

/** 卡片右上角类型图标（reference：链接→link / 报告→report / 其余→edit；按 §4.2 补全各类型）。 */
function thumbIcon(t: FavType): React.ReactNode {
  switch (t) {
    case '链接': return <IconLink />;
    case '报告': return <IconReport />;
    case '聊天记录': return <IconBook />;
    case '图片': case '视频': return <IconImage />;
    case '文件': return <IconFolder />;
    case '知识卡片': return <IconSpark />;
    default: return <IconEdit />;          // 笔记
  }
}

/**
 * 跳回来源消息（README §4.6）：切聊天视图 → 切会话 → 定位 .mgroup[data-seq] → .flash 闪烁。
 * 两帧 rAF 等视图切换/快照渲染就绪（与 ChatStream 的 jumpSeq 定位同一手法）；
 * flash 三连（remove → 强制重排 → add）照抄 reference flashItem，同一条连点也能重新闪。
 */
function jumpToMessage(entry: FavEntry): void {
  const ref = entry.ref;
  if (!ref?.projectId) { toast(i18n.t('favorites.view.noSource'), 'warn'); return; }
  useFavoritesStore.getState().touch(entry.id);
  const st = useKnoweStore.getState();
  st.setView('chats');
  st.switchProject(ref.projectId);
  if (ref.seq == null) return;             // 乐观消息还没 seq：能回到会话，定位不了具体气泡
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const node = document.querySelector<HTMLElement>(`.msgs .mgroup[data-seq="${ref.seq}"]`);
      if (!node) return;
      node.scrollIntoView({ behavior: 'smooth', block: 'center' });
      node.classList.remove('flash');
      void node.offsetWidth;
      node.classList.add('flash');
      window.setTimeout(() => node.classList.remove('flash'), 1700);
    });
  });
}

// ═══════════════════════════════════════════════════════════════

export interface FavoritesSearchFocus {
  favoriteId: string;
  requestId: number;
}

export interface FavoritesViewProps {
  searchFocus?: FavoritesSearchFocus | null;
  onSearchFocusDone?: (requestId: number) => void;
}

export const FavoritesView: React.FC<FavoritesViewProps> = ({
  searchFocus = null, onSearchFocusDone,
}) => {
  const { t } = useTranslation();
  const entries = useFavoritesStore((s) => s.entries);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState<CatId>('全部收藏');
  const [tag, setTag] = useState<string | null>(null);   // 标签筛选与分类互斥（选一即清另一）
  const [previewId, setPreviewId] = useState<string | null>(null);  // #8 右侧预览面板：当前打开的收藏

  // 标签清单 = 全部条目标签的并集（README §4.1：「标签」分组及其下的标签列表）。
  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const e of entries) for (const t of e.tags) s.add(t);
    return Array.from(s);
  }, [entries]);

  const kw = q.trim();
  const shown = useMemo(() => {
    let list = entries;
    list = tag
      ? list.filter((e) => e.tags.includes(tag))
      : list.filter((e) => inCat(e, cat));
    if (kw) list = list.filter((e) => e.title.includes(kw) || e.digest.includes(kw));
    if (!tag && cat === '最近使用') list = [...list].sort((a, b) => b.lastUsedAt - a.lastUsedAt);
    return list;
  }, [entries, cat, tag, kw]);

  // 分类/类型的显示名（内部标识符保持原文，仅显示层翻译）
  const catLabel = (id: CatId): string => {
    switch (id) {
      case '全部收藏': return t('favorites.view.04');
      case '最近使用': return t('favorites.view.16');
      case '链接': return t('common.13');
      case '图片与视频': return t('favorites.view.08');
      case '笔记': return t('favorites.view.03');
      case '文件': return t('common.02');
      case '聊天记录': return t('common.21');
      case '报告与产出': return t('favorites.view.14');
      case '知识卡片': return t('favorites.view.02');
    }
  };

  const typeLabel = (tp: FavType): string => {
    switch (tp) {
      case '链接': return t('common.13');
      case '报告': return t('common.08');
      case '聊天记录': return t('common.21');
      case '图片': return t('common.01');
      case '视频': return t('common.11');
      case '文件': return t('common.02');
      case '知识卡片': return t('favorites.view.02');
      default: return t('favorites.view.03');          // 笔记
    }
  };

  const headTitle = tag ?? catLabel(cat);
  const previewEntry = previewId ? entries.find((e) => e.id === previewId) ?? null : null;
  const handledSearchRequest = useRef<number | null>(null);

  // 全局搜索命中收藏后，撤掉本页筛选、打开既有预览，并把卡片滚到可见位置。
  useEffect(() => {
    if (!searchFocus || handledSearchRequest.current === searchFocus.requestId) return undefined;
    const entry = useFavoritesStore.getState().entries
      .find((candidate) => candidate.id === searchFocus.favoriteId);
    if (!entry) {
      onSearchFocusDone?.(searchFocus.requestId);
      return undefined;
    }
    handledSearchRequest.current = searchFocus.requestId;
    setQ('');
    setCat('全部收藏');
    setTag(null);
    setPreviewId(entry.id);
    useFavoritesStore.getState().touch(entry.id);

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const node = Array.from(document.querySelectorAll<HTMLElement>('[data-favorite-id]'))
          .find((candidate) => candidate.dataset.favoriteId === entry.id);
        node?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        onSearchFocusDone?.(searchFocus.requestId);
      });
    });
    return undefined;
  }, [onSearchFocusDone, searchFocus]);

  // #8/#9 一张收藏卡 → 一条「待转发内容」。文件类带文件，文本类带正文（Agent 富文本走 markdown）。
  // [v1.0.23.1] 补 sourceProject（FavEntry 已有字段）——引用窗 header 与 LLM 模板的来源群名。
  const favForwardItem = (entry: FavEntry): ForwardItem => ({
    text: entry.file ? entry.file.name : entry.body,
    files: entry.file ? [entry.file] : undefined,
    markdown: entry.markdown,
    sourceName: entry.sourceName,
    sourceProjectName: entry.sourceProject,
    sourceRef: entry.ref ? { projectId: entry.ref.projectId } : undefined,
  });

  const cardMenu = (e: React.MouseEvent, entry: FavEntry): void => {
    e.preventDefault();
    const items: MenuEntry[] = [
      // #8 「打开」→ 右侧预览面板（不再跳回聊天）。
      { icon: <Msg16 />, label: t('favorites.view.13'), onClick: () => { useFavoritesStore.getState().touch(entry.id); setPreviewId(entry.id); } },
      // #5 「重命名」→ 输入弹窗 → 更新标题。
      {
        icon: <IconEdit />, label: t('common.22'),
        onClick: () => openInputModal({
          title: t('favorites.view.21'),
          initial: entry.title,
          placeholder: t('favorites.view.19'),
          onOk: (name) => { useFavoritesStore.getState().rename(entry.id, name); toast(t('common.toastRenamed')); },
        }),
      },
      // #9 「转发」→ 真正转发（内容作为转发消息体）。
      {
        icon: <IconForward />, label: t('chat.stream.06'),
        onClick: () => { useFavoritesStore.getState().touch(entry.id); openForwardPicker([favForwardItem(entry)]); },
      },
      {
        icon: <IconTag />, label: t('context.menu.06'),
        onClick: () => openTagEditor(entry.tags, (tags) => {
          useFavoritesStore.getState().setTags(entry.id, tags);
          toast(t('favorites.view.tagUpdated'));
        }),
      },
      '---',
      {
        icon: <IconTrash />, danger: true, label: t('favorites.view.18'),
        onClick: () => {
          if (previewId === entry.id) setPreviewId(null);
          useFavoritesStore.getState().remove(entry.id);
          toast(t('favorites.view.unfavorited'));
        },
      },
    ];
    openMenu(items, e.clientX, e.clientY);
  };

  return (
    <>
      {/* ═══ 左侧导航（SidePanel） ═══ */}
      <aside className="side">
        <div className="side-head"><div className="side-title">{t('common.09')}</div></div>

        <div className="search-wrap">
          <div className="search">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" />
            </svg>
            <input
              placeholder={t('favorites.view.15')}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label={t('favorites.view.15')}
            />
          </div>
        </div>

        {/* [v0.40.2 #3] 「＋ 新建笔记」：宽 100%、外层 0 16px 8px —— 照抄 reference（3067 行）。
            点击弹出笔记编辑弹窗（支持 markdown + 实时预览），确认后生成一张「笔记」卡。 */}
        <div style={{ padding: '0 16px 8px' }}>
          <button
            className="btn btn-primary"
            style={{ width: '100%' }}
            onClick={() => openNoteComposer((text) => {
              useFavoritesStore.getState().addNote(text);
              toast(t('favorites.view.noteSaved'));
            })}
          >
            {t('favorites.view.newNote')}
          </button>
        </div>

        <div className="side-scroll">
          {CATS.map((c) => {
            const n = entries.filter((e) => inCat(e, c.id)).length;
            const active = !tag && cat === c.id;
            return (
              <div
                key={c.id}
                className={'navrow' + (active ? ' active' : '')}
                role="button"
                tabIndex={0}
                onClick={() => { setCat(c.id); setTag(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { setCat(c.id); setTag(null); } }}
              >
                <span className="navrow-ic">{c.icon}</span>
                <span className="navrow-nm">{catLabel(c.id)}</span>
                <span className="navrow-cnt">{n}</span>
              </div>
            );
          })}

          <div className="sec-head">
            <span className="chev"><ChevD /></span>{t('favorites.view.tagLabel')}
          </div>
          {allTags.length === 0 ? (
            <div className="navrow" aria-disabled="true" style={{ cursor: 'default', color: 'var(--ink-3)' }}>
              <span className="navrow-ic"><IconTag /></span>
              <span className="navrow-nm">{t('favorites.view.06')}</span>
            </div>
          ) : (
            allTags.map((t) => {
              const n = entries.filter((e) => e.tags.includes(t)).length;
              return (
                <div
                  key={t}
                  className={'navrow' + (tag === t ? ' active' : '')}
                  role="button"
                  tabIndex={0}
                  onClick={() => setTag((cur) => (cur === t ? null : t))}
                  onKeyDown={(e) => { if (e.key === 'Enter') setTag((cur) => (cur === t ? null : t)); }}
                >
                  <span className="navrow-ic"><IconTag /></span>
                  <span className="navrow-nm">{t}</span>
                  <span className="navrow-cnt">{n}</span>
                </div>
              );
            })
          )}
        </div>
    </aside>

      {/* ═══ 右侧内容区（Stage） ═══ */}
      <div className="stage">
        <div className="stage-card">
          <div className="stage-head">
            <div>
              <div className="stage-h1">{headTitle}</div>
              <div className="stage-sub">{t('favorites.view.stageSummary', { n: shown.length })}</div>
            </div>
          </div>

          <div className="stage-scroll">
            {shown.length === 0 ? (
              <div className="empty2">
                <div className="e-ic"><IconStar /></div>
                <p>
                  {entries.length === 0
                    ? t('favorites.view.20')
                    : t('favorites.view.17')}
                </p>
              </div>
            ) : (
              shown.map((e) => (
                <div
                  key={e.id}
                  data-favorite-id={e.id}
                  className={'card' + (previewId === e.id ? ' fav-card-active' : '')}
                  onContextMenu={(ev) => cardMenu(ev, e)}
                  onDoubleClick={() => { useFavoritesStore.getState().touch(e.id); setPreviewId(e.id); }}
                  title={t('favorites.view.05')}
                >
                  <div className="card-top">
                    <div className="card-title">{e.title}</div>
                    <div className="card-thumb">{thumbIcon(e.type)}</div>
                  </div>
                  {/* #7 正文摘要：Agent 文字气泡保留 markdown 渲染；其余纯文本。 */}
                  <div className="card-body">
                    {e.markdown ? <div className="fav-md"><Markdown text={e.digest} /></div> : e.digest}
                  </div>
                  <div className="card-meta">
                    {/* 18px 小头像：reference 用 .avatar av-{pal} 内联缩成 18×18、font-size 0 */}
                    <span
                      className={`avatar ${e.pal}`}
                      style={{ width: 18, height: 18, fontSize: 0, flexShrink: 0 }}
                      aria-hidden="true"
                    />
                    <span
                      className="lk"
                      role="button"
                      tabIndex={0}
                      title={t('favorites.view.07')}
                      onClick={() => jumpToMessage(e)}
                      onKeyDown={(ev) => { if (ev.key === 'Enter') jumpToMessage(e); }}
                    >
                      {e.sourceProject ? `${e.sourceProject} · ${e.sourceName}` : e.sourceName}
                    </span>
                    <span className="sep">·</span>
                    <span>{typeLabel(e.type)}</span>
                    <span style={{ marginLeft: 'auto' }}>{favDateLabel(e.addedAt)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ═══ [v0.40.1 #8] 右侧预览面板 ═══
            「打开」/双击卡片 → 在收藏界面右侧就地预览，不跳回聊天。
            文本/聊天记录 → Markdown；图片/视频/文件 → 复用应用既有 FileCard（点开走 PreviewPanel/下载）。 */}
        <FavPreview entry={previewEntry} onClose={() => setPreviewId(null)} />
      </div>
    </>
  );
};

/** 收藏预览面板：文本走 Markdown；文件类**直接预览文件真实内容**（#6）。 */
const FavPreview: React.FC<{ entry: FavEntry | null; onClose: () => void }> = ({ entry, onClose }) => {
  const { t } = useTranslation();
  const fileHostRef = useRef<HTMLDivElement>(null);

  /*
   * [v0.40.2 #6] ★ 打开文件收藏 = 预览文件**里面的真实内容**（图片看图片、PDF 看 PDF、
   *   文档看文档），行为与在聊天界面点击文件卡片进行预览**完全一致**。
   *
   *   文件卡片的点击本就会打开应用既有的文件预览（PreviewPanel / 下载）——所以这里在面板
   *   挂载后**自动触发它一次**，让「打开」直达文件内容，而不是停在卡片摘要上（旧行为的毛病）。
   *   点最深的叶子节点：原生 click 会冒泡到文件卡的可点击祖先，触发其 onClick（无论 handler
   *   挂在哪一层）。若某些环境没自动弹出，卡片仍留在面板里，可手动点一下兜底。
   */
  useEffect(() => {
    if (!entry?.file) return;
    const host = fileHostRef.current;
    if (!host) return;
    const card = host.firstElementChild as HTMLElement | null;   // FileCard 根节点
    if (!card) return;
    let leaf: Element = card;
    while (leaf.firstElementChild) leaf = leaf.firstElementChild;
    const t = window.setTimeout(() => (leaf as HTMLElement).click(), 0);
    return () => window.clearTimeout(t);
  }, [entry?.id]);   // 每换一张文件收藏触发一次

  return (
    <div className={'fav-preview' + (entry ? ' open' : '')} aria-hidden={!entry}>
      {entry && (
        <>
          <div className="fav-preview-head">
            <div className="fav-preview-title" title={entry.title}>{entry.title}</div>
            <button className="icon-btn" aria-label={t('favorites.view.01')} onClick={onClose}><IconX /></button>
          </div>
          <div className="fav-preview-body">
            {entry.file ? (
              <div className="fav-preview-file" ref={fileHostRef}>
                <FileCard file={entry.file} projectId={entry.sourceProjectId || ''} />
                <p className="fav-preview-hint">
                  {entry.type === '图片' ? t('favorites.view.09')
                    : entry.type === '视频' ? t('favorites.view.11')
                      : t('favorites.view.10')}
                </p>
              </div>
            ) : entry.markdown ? (
              <div className="fav-md"><Markdown text={entry.body || entry.digest} /></div>
            ) : (
              <div className="fav-preview-text">{entry.body || entry.digest}</div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default FavoritesView;
