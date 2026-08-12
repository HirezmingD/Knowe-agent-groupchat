// src/store/records.ts
// [v0.38] 聊天记录抽屉 —— 轻量独立 store（zustand，与 useKnoweStore 同一套用法）。
//
// 数据源：后端 8081 端口的 /history 端点（与 /preview 同一个本地 HTTP 服务）。

import { runtimeHttpBase } from '../shared/runtimeEndpoints';
import { runtimeFetch } from '../shared/runtimeFetch';
import { create } from 'zustand';
import type { ProducedFile } from './state';   // [v0.38.1 #9] 复用聊天区的文件类型
import i18n from '../i18n';

/** 一条历史记录（对应后端 /history 响应里的 item）。 */
export interface HistoryItem {
  seq: number;
  type: string;            // 'message' | 'user_echo'
  agent_id: string;        // user_echo 为空
  content: string;
  ts: number | null;       // 毫秒；来自事件 ts，可能为 null（老事件没带 ts）
  has_files: boolean;
  has_images: boolean;
  has_videos: boolean;
  has_links: boolean;
  /** [v0.38.1 #9] 本条消息附带的产出文件（后端在事件含文件时附上）。用于文件筛选卡片 + 点击预览。 */
  files?: ProducedFile[];
}

/** [v0.38.3 #4] 一条「报告/交接」：instruction + report 配成一对。 */
export interface HandoffItem {
  id: string | number;
  seq: number;
  ts: number | null;
  title: string;
  instruction: string;
  report: string;
  agent_id: string;
}

export type RecordsCategory = 'all' | 'files' | 'images' | 'videos' | 'links' | 'date' | 'handoff';

export const RECORDS_TABS: { key: RecordsCategory; label: string }[] = [
  { key: 'all', label: 'common.15' },
  { key: 'files', label: 'common.02' },
  { key: 'images', label: 'common.01' },
  { key: 'videos', label: 'common.11' },
  { key: 'links', label: 'common.13' },
  { key: 'date', label: 'range.calendar.01' },
];

export type FileGroupMode = 'sender' | 'date';

/** 文件 tab 的纯前端分组投影；不改变 /history 返回结构与分页协议。 */
export interface FileHistoryGroup {
  key: string;
  mode: FileGroupMode;
  /** sender 模式：空字符串表示屏幕前用户。 */
  senderId?: string;
  /** date 模式：本地日期 YYYY-MM-DD；null 表示老记录缺失时间。 */
  dateKey?: string | null;
  items: HistoryItem[];
  messageCount: number;
  fileCount: number;
}

function localDateKey(ts: number | null): string | null {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return null;
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return null;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * 把当前已加载的消息按发送人或本地日期归组。Map 的插入顺序保留后端时间线顺序，
 * 因而分页追加时既不会重排已有分组，也不会制造一套新的服务端排序规则。
 * [v1.0.24.1] includeAll=true 时不过滤无文件消息——全部/图片/视频/链接 tab 复用同一分组。
 */
export function groupFileHistory(
  items: HistoryItem[],
  mode: FileGroupMode,
  includeAll = false,
): FileHistoryGroup[] {
  const groups = new Map<string, FileHistoryGroup>();

  for (const item of items) {
    const fileCount = Array.isArray(item.files) ? item.files.length : 0;
    if (!includeAll && !fileCount) continue;

    const isUser = item.type === 'user_echo' || !item.agent_id;
    const senderId = isUser ? '' : item.agent_id;
    const dateKey = localDateKey(item.ts);
    const key = mode === 'sender'
      ? (senderId ? `sender:agent:${senderId}` : 'sender:user')
      : `date:${dateKey || '__unknown__'}`;

    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        mode,
        ...(mode === 'sender' ? { senderId } : { dateKey }),
        items: [],
        messageCount: 0,
        fileCount: 0,
      };
      groups.set(key, group);
    }
    group.items.push(item);
    group.messageCount += 1;
    group.fileCount += fileCount;
  }

  return [...groups.values()];
}

// ── 端点基址 ─────────────────────────────────────────────────────────────────
//  /history 和 /preview 挂在同一个本地 HTTP 服务上（CONFIG.health_host:health_port，
//  默认 127.0.0.1:8081）。若你把预览 URL 的基址集中在某个常量/配置里（FileCard 拼
//  /preview 时用的那个），把这里换成同一个来源即可。
export function historyBase(): string { return runtimeHttpBase(); }

interface RecordsState {
  open: boolean;
  category: RecordsCategory;
  searchQuery: string;
  selectedDate: string | null;   // YYYY-MM-DD
  page: number;
  pageSize: number;
  items: HistoryItem[];
  total: number;
  loading: boolean;
  error: string | null;
  /** [v0.38.3 #3] 请求跳转到某条消息（按 seq）。ChatStream 消费后清空。 */
  jumpSeq: number | null;
  /** [v0.38.3 #4] 报告/交接列表。 */
  handoffs: HandoffItem[];
  handoffsLoading: boolean;

  openDrawer: () => void;
  closeDrawer: () => void;
  toggleDrawer: () => void;
  setCategory: (c: RecordsCategory) => void;
  setSearchQuery: (q: string) => void;
  setSelectedDate: (d: string | null) => void;
  requestJump: (seq: number) => void;
  clearJump: () => void;
  load: (projectId: string) => Promise<void>;
  loadMore: (projectId: string) => Promise<void>;
  loadHandoffs: (projectId: string) => Promise<void>;
  reset: () => void;
}

const DEFAULT_PAGE_SIZE = 25;

function buildUrl(p: {
  projectId: string; category: RecordsCategory; page: number; pageSize: number; date: string | null;
}): string {
  const q = new URLSearchParams();
  q.set('project_id', p.projectId);
  if (p.category !== 'all' && p.category !== 'date' && p.category !== 'handoff') {
    q.set('category', p.category);
  }
  q.set('page', String(p.page));
  q.set('page_size', String(p.pageSize));
  if (p.date) q.set('date', p.date);
  return `${historyBase()}/history?${q.toString()}`;
}

async function fetchHistory(p: {
  projectId: string; category: RecordsCategory; page: number; pageSize: number; date: string | null;
}): Promise<{ items: HistoryItem[]; total: number }> {
  const res = await runtimeFetch(buildUrl(p), { method: 'GET' });
  if (!res.ok) throw new Error(`history ${res.status}`);
  const data = await res.json();
  return {
    items: Array.isArray(data.items) ? (data.items as HistoryItem[]) : [],
    total: typeof data.total === 'number' ? data.total : 0,
  };
}

export const useRecordsStore = create<RecordsState>((set, get) => ({
  open: false,
  category: 'all',
  searchQuery: '',
  selectedDate: null,
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  items: [],
  total: 0,
  loading: false,
  error: null,
  jumpSeq: null,
  handoffs: [],
  handoffsLoading: false,

  openDrawer: () => set({ open: true }),
  closeDrawer: () => set({ open: false }),
  toggleDrawer: () => set((s) => ({ open: !s.open })),

  // [v0.38.1 #9] 文件筛选每页最多 10 条；其余分类沿用默认 25。
  setCategory: (c) => set({
    category: c, page: 1, items: [], total: 0, error: null,
    pageSize: c === 'files' ? 10 : DEFAULT_PAGE_SIZE,
  }),
  setSearchQuery: (q) => set({ searchQuery: q }),
  setSelectedDate: (d) => set({ selectedDate: d, page: 1, items: [], total: 0, error: null }),
  requestJump: (seq) => set({ jumpSeq: seq }),
  clearJump: () => set({ jumpSeq: null }),

  async load(projectId) {
    if (!projectId) return;
    const { category, pageSize, selectedDate } = get();
    set({ loading: true, error: null, page: 1 });
    try {
      const { items, total } = await fetchHistory({
        projectId, category, page: 1, pageSize,
        date: category === 'date' ? selectedDate : null,
      });
      set({ items, total, page: 1, loading: false });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : i18n.t('html.preview.01') });
    }
  },

  async loadMore(projectId) {
    if (!projectId) return;
    const { category, pageSize, selectedDate, page, items, total, loading } = get();
    if (loading || items.length >= total) return;
    const nextPage = page + 1;
    set({ loading: true, error: null });
    try {
      const res = await fetchHistory({
        projectId, category, page: nextPage, pageSize,
        date: category === 'date' ? selectedDate : null,
      });
      set({ items: [...items, ...res.items], total: res.total, page: nextPage, loading: false });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : i18n.t('html.preview.01') });
    }
  },

  // [v0.38.3 #4] 拉取报告/交接（一次全量，前端不分页——通常条数不多）。
  async loadHandoffs(projectId) {
    if (!projectId) return;
    set({ handoffsLoading: true, error: null });
    try {
      const res = await runtimeFetch(`${historyBase()}/history/handoffs?project_id=${encodeURIComponent(projectId)}`);
      if (!res.ok) throw new Error(`handoffs ${res.status}`);
      const data = await res.json();
      set({
        handoffs: Array.isArray(data.items) ? (data.items as HandoffItem[]) : [],
        handoffsLoading: false,
      });
    } catch (e) {
      set({ handoffsLoading: false, error: e instanceof Error ? e.message : i18n.t('html.preview.01') });
    }
  },

  reset: () => set({
    category: 'all', searchQuery: '', selectedDate: null,
    page: 1, items: [], total: 0, loading: false, error: null,
    handoffs: [], jumpSeq: null,
  }),
}));

/** 结合本地搜索词过滤已加载项；文件名/路径与发送人 id 也可命中。 */
export function filterBySearch(items: HistoryItem[], query: string): HistoryItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((item) => {
    if ((item.content || '').toLowerCase().includes(q)) return true;
    if ((item.agent_id || '').toLowerCase().includes(q)) return true;
    return (item.files || []).some((file) => (
      [file.name, file.path, file.ext, file.kind, file.media_type]
        .some((value) => typeof value === 'string' && value.toLowerCase().includes(q))
    ));
  });
}
