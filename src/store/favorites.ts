/**
 * favorites.ts — [v0.40.0 / v0.40.1] 收藏数据 store（README §四/§五）
 *
 * 条目字段照 README §五：标题、摘要、来源名称、来源项目名（可选）、头像配色、类型、
 * 收藏日期、消息引用（跳回原消息用）、用户标签（可选）。
 *
 * [v0.40.1 修复]
 *   · #6 文件/图片/视频收藏：右键的是**具体一张文件卡** → 标题取文件名（图片/视频→「图片」「视频」）、
 *        摘要为「类型 · 大小」，类型入对应分类；不再误带整条消息的文字。
 *   · #7 整条 Agent 文字气泡收藏：默认标题「{群名} - {Agent名} · {职能} - 发言」，正文保留 markdown
 *        （FavEntry.markdown=true，卡片 / 预览按 Markdown 渲染）。
 *   · #3 多选合并：多条消息并成**一张**卡（addMerged），摘要是各条概览。
 *   · #5 重命名：rename(id, title)。
 *   · #8 预览：文件类收藏存下 ProducedFile + projectId，预览面板用既有 FileCard 打开。
 *
 * ⚠ 初期**前端内存存储**（README §五明说不写后端）：刷新 / 重启即空。
 *   // TODO: favorites 持久化 API（后端就绪后 add/remove/setTags/rename 改发命令，启动拉全量）
 */

import { create } from 'zustand';
import type { Conv, Item, ProducedFile } from './state';
import { itemKeyOf } from './state';
import { PLATFORM_PROJECT_ID } from './avatar';
import i18n from '../i18n';

/** 卡片类型（README §4.2 的图标清单 + 知识卡片）。 */
export type FavType = '链接' | '图片' | '视频' | '笔记' | '文件' | '聊天记录' | '报告' | '知识卡片';

export interface FavEntry {
  id: string;
  /** 卡片标题（可被「重命名」改写）。 */
  title: string;
  /** 卡片摘要（≤200 字预览；文件类为「类型 · 大小」）。 */
  digest: string;
  /** 预览面板用的完整正文（文本/聊天记录/报告/笔记；文件类为空）。 */
  body: string;
  /** 正文是否按 Markdown 渲染（Agent 文字气泡 = true）。 */
  markdown: boolean;
  /** 来源名称（「我」/ Agent 显示名 / 「链接」）。 */
  sourceName: string;
  /** 来源项目名（群聊消息才有；知知/私聊不挂群名）。 */
  sourceProject?: string;
  /** 来源头像配色（card-meta 里那颗 18px 小圆点用）。 */
  pal: string;
  type: FavType;
  /** 收藏时刻（ms）。展示用「刚刚/今天/昨天/M月D日」由组件层格式化。 */
  addedAt: number;
  /** 最近一次使用（打开/转发）时刻——「最近使用」分类按它算。 */
  lastUsedAt: number;
  /** 消息引用：跳回原消息（seq 标在 .mgroup 的 data-seq 上）。 */
  ref?: { projectId: string; seq?: number };
  /** [v0.40.1] 文件类收藏的原文件 + 所在项目——预览面板用 FileCard 打开（图片/视频/文件）。 */
  file?: ProducedFile;
  sourceProjectId?: string;
  /** 用户标签（编辑标签弹窗写入；形如 '#设计'）。 */
  tags: string[];
}

let _id = 0;
const nextId = (): string => `fav_${Date.now().toString(36)}_${(_id += 1).toString(36)}`;

/** 纯 URL（或以 URL 开头的一行）→ 按「链接」收藏（README §4.2 类型含链接）。 */
function looksLikeLink(text: string): boolean {
  return /^https?:\/\/\S+$/.test(text.trim());
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const VIDEO_EXT = /\.(mp4|mov|webm|mkv|avi)$/i;

/** 文件 → 收藏类型。 */
export function favTypeOfFile(f: ProducedFile): FavType {
  if (IMAGE_EXT.test(f.name)) return '图片';
  if (VIDEO_EXT.test(f.name)) return '视频';
  return '文件';
}

/** 人类可读大小（favorites 自带一份，避免从 components/preview 反向依赖）。 */
export function humanSize(bytes?: number): string {
  if (bytes == null || bytes <= 0) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let n = bytes; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

/** 文件类收藏的「类型 · 大小」摘要。 */
function fileDigest(f: ProducedFile, type: FavType): string {
  const size = humanSize(f.bytes);
  const ext = (f.name.match(/\.([a-z0-9]+)$/i)?.[1] || '').toUpperCase();
  const kindWord = type === '图片' ? i18n.t('common.01') : type === '视频' ? i18n.t('common.11') : `${ext || ''}${i18n.t('common.02')}`.trim();
  return size ? `${kindWord} · ${size}` : kindWord;
}

interface FavoritesState {
  entries: FavEntry[];

  /**
   * 右键消息 →「收藏」（README §4.3）。
   *
   *   · file 给定（右键落在某张文件卡/图片/视频上）→ #6：标题取文件名（图片/视频→「图片」「视频」），
   *     摘要「类型 · 大小」，类型入对应分类；忽略消息文字。
   *   · 否则整条消息：Agent 文字气泡走 #7 标题（titleParts），正文保留 markdown；
   *     纯 URL → 链接；其余 → 聊天记录。
   * source（名字/配色/群名）由调用方给（ChatStream 手上有 face，store 不重复算脸）。
   */
  addFromMessage: (args: {
    conv: Conv;
    item: Item;
    sourceName: string;
    pal: string;
    isGroup: boolean;
    /** 右键落点的具体文件（文件卡/图片/视频收藏时给）。 */
    file?: ProducedFile;
    /** Agent 文字气泡的默认标题片段：群名/知知 + Agent名 + 职能。给了就拼「X - Y · Z - 发言」。 */
    titleParts?: { groupLabel: string; agentName: string; role: string };
  }) => void;

  /** [v0.40.1 #3 / v0.40.2 #5] 多选合并为一张卡：正文保留每条**完整原文** + 发言人名字。 */
  addMerged: (args: {
    conv: Conv;
    items: Item[];
    isGroup: boolean;
    pal: string;
  }) => void;

  /**
   * [v0.40.2 #3] 新建笔记：收藏页「＋ 新建笔记」弹窗确认后调用，生成一张「笔记」卡。
   *   内容支持 markdown（卡片/预览按 markdown 渲染）；来源是用户自己（显示「我」）。
   */
  addNote: (text: string) => void;

  remove: (id: string) => void;
  setTags: (id: string, tags: string[]) => void;
  /** [v0.40.1 #5] 重命名卡片标题。 */
  rename: (id: string, title: string) => void;
  /** 「打开/转发」时点一下——「最近使用」分类据此排序。 */
  touch: (id: string) => void;
}

/**
 * [v0.40.2 #7] 这条会话归属项目的展示名：私聊 → 父群名（parentProjectName）；
 * 群聊 → 本会话项目名。用于收藏卡的来源前缀（#8）。
 */
function projectLabelOf(conv: Conv): string {
  return conv.parentProjectName || conv.projectName || conv.projectId;
}

/**
 * [v0.40.2 #8] 收藏卡底部「来源名称」的解析：
 *   · 用户自己（user 气泡）      → 「我」，无项目前缀
 *   · 知知（平台会话里的气泡）   → 「知知」，无项目前缀
 *   · 项目内 Agent（群聊/私聊）  → sourceName=Agent名，sourceProject=项目名（私聊取父群名）
 * FavoritesView 渲染成 `项目名 · Agent名` / `我` / `知知`。
 */
function favSource(conv: Conv, item: Item, rawName: string): { name: string; project?: string } {
  if (item.kind === 'user') return { name: i18n.t('common.me') };
  if (conv.projectId === PLATFORM_PROJECT_ID) return { name: i18n.t('common.10') };
  return { name: rawName, project: projectLabelOf(conv) };
}

/** [v0.40.2 #5] 合并卡里一条消息的发言人名字：user→「我」；agent→花名册显示名（兜底 id）。 */
function mergeSpeaker(conv: Conv, it: Item): string {
  if (it.kind === 'user') return i18n.t('common.me');
  if (it.kind === 'agent') {
    const m = conv.members?.find((x) => x.id === it.agentId);
    return m?.display.name || it.agentId;
  }
  return '';
}

/** [v0.40.2 #5] 文件在合并正文里的标注：图片→[图片]、视频→[视频]、其余→[文件: 名]。 */
function fileMarker(f: ProducedFile): string {
  const t = favTypeOfFile(f);
  if (t === '图片') return i18n.t('favorites.01');
  if (t === '视频') return i18n.t('favorites.02');
  return i18n.t('favorites.fileMarker', { name: f.name });
}

/** [v0.40.2 #5] 合并卡里一条消息的**完整正文**：完整原文 + 文件标注（换行分隔）。 */
function mergeBody(it: Item): string {
  const parts: string[] = [];
  const text = (it.kind === 'user' || it.kind === 'agent') ? (it.text || '').trim() : '';
  if (text) parts.push(text);
  const files = (it.kind === 'user' || it.kind === 'agent') ? (it.files || []) : [];
  for (const f of files) parts.push(fileMarker(f));
  return parts.join('\n') || i18n.t('favorites.07');
}

export const useFavoritesStore = create<FavoritesState>((set) => ({
  entries: [],

  addFromMessage({ conv, item, sourceName, pal, file, titleParts }) {
    if (item.kind !== 'user' && item.kind !== 'agent') return;
    const now = Date.now();
    // #8 来源名 + 项目前缀（user→「我」、知知→「知知」、Agent→项目名 · Agent名）。
    const src = favSource(conv, item, sourceName);
    let entry: FavEntry;

    if (file) {
      // #6 具体文件卡收藏：标题=文件名（图片/视频→「图片」「视频」），摘要=类型·大小。
      const type = favTypeOfFile(file);
      const title = type === '图片' ? i18n.t('common.01') : type === '视频' ? i18n.t('common.11') : file.name;
      entry = {
        id: nextId(),
        title,
        digest: fileDigest(file, type),
        body: '',
        markdown: false,
        sourceName: src.name,
        pal,
        type,
        addedAt: now,
        lastUsedAt: now,
        tags: [],
        ref: { projectId: conv.projectId, seq: item.seq },
        file,
        sourceProjectId: conv.projectId,
      };
    } else {
      const text = (item.text || '').trim();
      const type: FavType = looksLikeLink(text) ? '链接' : '聊天记录';
      // #7 Agent 文字气泡默认标题「群名 - Agent名 · 职能 - 发言」；否则原文前 18 字。
      //   groupLabel 由 ChatStream 传入，私聊已取父群名（不再误显示「知知」）。
      let title: string;
      if (titleParts) {
        const { groupLabel, agentName, role } = titleParts;
        const who = role ? `${agentName} · ${role}` : agentName;
        title = groupLabel && groupLabel !== agentName
          ? i18n.t('favorites.mergedTitle', { group: groupLabel, who })
          : i18n.t('favorites.mergedTitle', { group: '', who });
      } else {
        title = text.length > 18 ? `${text.slice(0, 18)}…` : (text || i18n.t('favorites.07'));
      }
      entry = {
        id: nextId(),
        title,
        digest: text.slice(0, 200),
        body: text,
        // #7 保留 markdown：Agent 文字气泡默认按 markdown 渲染（用户自己的字不做 md）。
        markdown: item.kind === 'agent' && !!titleParts,
        sourceName: src.name,
        pal,
        type,
        addedAt: now,
        lastUsedAt: now,
        tags: [],
        ref: { projectId: conv.projectId, seq: item.seq },
      };
    }
    if (src.project) entry.sourceProject = src.project;
    set((s) => ({ entries: [entry, ...s.entries] }));
  },

  addMerged({ conv, items, pal }) {
    const msgs = items.filter((it) => it.kind === 'user' || it.kind === 'agent');
    if (msgs.length === 0) return;
    const now = Date.now();
    // #5 每条保留**完整原文** + 发言人名字（文件标注 [文件: 名]/[图片]/[视频]），换行分隔。
    //   卡面显示的是 digest（前 200 字预览），完整原文在 body（打开预览面板可见全文）。
    const body = msgs.map((it) => `${mergeSpeaker(conv, it)}：${mergeBody(it)}`).join('\n');
    const entry: FavEntry = {
      id: nextId(),
      title: i18n.t('favorites.mergedCount', { n: msgs.length }),
      digest: body.slice(0, 200),
      body,
      markdown: false,
      sourceName: i18n.t('favorites.05'),
      pal,
      type: '聊天记录',
      addedAt: now,
      lastUsedAt: now,
      tags: [],
      ref: { projectId: conv.projectId },
    };
    // #8 合集来源加项目前缀（私聊取父群名）；平台会话不加前缀。
    if (conv.projectId !== PLATFORM_PROJECT_ID) entry.sourceProject = projectLabelOf(conv);
    set((s) => ({ entries: [entry, ...s.entries] }));
  },

  addNote(text) {
    const t = (text || '').trim();
    if (!t) return;
    const now = Date.now();
    // 标题：取首个非空行，去掉常见 markdown 前缀符（#、-、1.、>、``` 等）后截断；空则「新笔记」。
    const firstLine = t.split('\n').map((s) => s.trim()).find(Boolean) || '';
    const clean = firstLine
      .replace(/^#{1,6}\s*/, '')
      .replace(/^[-*+]\s+/, '')
      .replace(/^\d+\.\s+/, '')
      .replace(/^>\s?/, '')
      .replace(/^`{3,}.*$/, '')
      .trim();
    const title = clean ? (clean.length > 18 ? `${clean.slice(0, 18)}…` : clean) : i18n.t('favorites.06');
    const entry: FavEntry = {
      id: nextId(),
      title,
      digest: t.slice(0, 200),
      body: t,
      markdown: true,       // 笔记支持 markdown → 卡片摘要/预览面板都按 markdown 渲染
      sourceName: i18n.t('common.me'),      // #8 笔记是用户自己写的：来源显示「我」，无项目前缀
      pal: 'av-a',
      type: '笔记',
      addedAt: now,
      lastUsedAt: now,
      tags: [],
    };
    set((s) => ({ entries: [entry, ...s.entries] }));
  },

  remove(id) {
    set((s) => ({ entries: s.entries.filter((e) => e.id !== id) }));
  },

  setTags(id, tags) {
    set((s) => ({
      entries: s.entries.map((e) => (e.id === id ? { ...e, tags: [...tags] } : e)),
    }));
  },

  rename(id, title) {
    const t = title.trim();
    if (!t) return;
    set((s) => ({
      entries: s.entries.map((e) => (e.id === id ? { ...e, title: t } : e)),
    }));
  },

  touch(id) {
    const now = Date.now();
    set((s) => ({
      entries: s.entries.map((e) => (e.id === id ? { ...e, lastUsedAt: now } : e)),
    }));
  },
}));

/** 收藏日期展示：刚刚（<3 分钟）/ 今天 / 昨天 / M月D日。 */
export function favDateLabel(ms: number): string {
  const now = new Date();
  const d = new Date(ms);
  if (Date.now() - ms < 3 * 60_000) return i18n.t('favorites.03');
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  if (sameDay) return i18n.t('favorites.04');
  const y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const isYesterday = d.getFullYear() === y.getFullYear()
    && d.getMonth() === y.getMonth() && d.getDate() === y.getDate();
  if (isYesterday) return i18n.t('common.17');
  return i18n.t('common.dateShort', { m: d.getMonth() + 1, d: d.getDate() });
}

/** 「最近使用」的窗口：7 天内被添加或打开过。 */
export const RECENT_WINDOW_MS = 7 * 24 * 3600_000;

// itemKeyOf re-export：ChatStream 收藏时和多选共用同一把钥匙（占位，防止误配第二套）。
export { itemKeyOf };
