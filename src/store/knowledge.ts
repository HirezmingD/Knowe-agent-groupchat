/**
 * knowledge.ts — 知识库视图的数据层（v0.43，知识资产 + 技能包）
 *
 * ★ 主数据源从**情节层节点**换成了**知识资产**（设计报告 §四）：
 *   卡片 = knowledge_assets.py 蒸馏并经用户策展的五类可复用资产
 *   （preference/playbook/pitfall/fact/decision），不再是 handoff 切片。
 *   情节层仍在（证据深钻走 /nodes /node 端点），但不再冒充知识上墙。
 *
 * 端点（knowledge_api.py v0.42，本机 HTTP；WS 契约照旧不碰）：
 *   GET  /assets                         合并资产快照（挂载即拉取 + 45s 轮询）
 *   GET  /{pid}/assets/{aid}             L1 全文（body_md，「预览」面板用）
 *   POST /{pid}/assets/{aid}             重命名 / 调整范围(scope+category) / 停用恢复
 *   POST /{pid}/assets/{aid}/review      待审策展：approve / reject
 *   POST /{pid}/assets/{aid}/purge       彻底删除（不可逆）
 *   GET  /{pid}/profile                  画像层 PROFILE.md 全文
 *   GET  /skillpacks                     三类真实技能：系统自备 / 项目经验 / 第三方
 *
 * 五类资产 → 设计稿三级标签（UI 四类，用户可在「调整范围」里改，改的是价值判断）：
 *   preference/decision → 约定(info)   pitfall → 坑(warn)
 *   playbook            → 模式(acc)    fact    → 清单(ok)
 *   后端已把 user_category 折算进 category 字段，前端直接用。
 *
 * 三态（生效中/待审/已退役）由后端 view_state 直给：
 *   candidate 或 needs_review/retire_suggested → pending（待审第一次有了真实供给）
 *   validated/core → ok；retired → retired
 *
 * 「被引用 N 次」= 真实 usage 事件数（use_count）；「来源 M 份」挪进 tooltip
 * ——两个数字终于各说各话（报告 §4.5）。
 */

import { create } from 'zustand';
import { runtimeHttpBase } from '../shared/runtimeEndpoints';
import { runtimeFetch } from '../shared/runtimeFetch';
import i18n from '../i18n';

// ═══════════════════════════════════════════════════════════════
// 视图模型
// ═══════════════════════════════════════════════════════════════

export type KnowCat = '约定' | '坑' | '模式' | '清单';
export type KnowChip = 'info' | 'warn' | 'acc' | 'ok';
export type KnowSt = 'ok' | 'pending' | 'retired';
export type KnowScope = 'global' | 'project';
export type AssetStatus = 'seed' | 'candidate' | 'validated' | 'core' | 'retired';

export interface KnowEvidence {
  sourceRef: string;
  nodeId: string;
  sourceKind: 'instruction' | 'report' | 'approval' | string;
  step: number | null;
  observedAt: string | null;
  excerpt: string;
  agentId: string | null;
}

export interface UsageEvent {
  kind: 'used_and_approved' | 'used_and_rejected' | 'declared_not_helpful'
  | 'matched_never_used' | string;
  step: number | null;
  at: string | null;
}

export interface KnowCard {
  /** `${projectId}:${assetId}`（资产 id 只在项目内唯一） */
  id: string;
  assetId: string;
  projectId: string;
  /** 后端五类之一 + 中文名（tooltip 用，四类归并不吞真实类型） */
  cls: string;
  clsZh: string;
  cat: KnowCat;
  chip: KnowChip;
  title: string;
  /** 卡片正文 = one_liner（L0 索引行；全文在「预览」里） */
  body: string;
  appliesWhen: string;
  status: AssetStatus;
  st: KnowSt;
  needsReview: boolean;
  retireSuggested: boolean;
  conflictWith: string[];
  scope: KnowScope;
  scopeSetBy: 'system' | 'user';
  utility: number;
  /** 被引用 N 次 = 真实引用（usage 事件） */
  cites: number;
  citedOk: number;
  citedBad: number;
  /** 来源 M 份（证据），tooltip 用 */
  sourceCount: number;
  firstSeen: string | null;
  lastSeen: string | null;
  evidence: KnowEvidence[];
}

/** 「预览」面板的详情（L1）：卡片字段 + ASSET.md 全文 + 使用轨迹 */
export interface KnowAssetDetail {
  card: KnowCard;
  bodyMd: string;
  usage: UsageEvent[];
}

export type SkillPackKind = 'system_builtin' | 'project_experience' | 'third_party';
export type SkillPackStatus = 'active' | 'pending' | 'retired';

export interface SkillPack {
  packId: string;
  name: string;
  description: string;
  kind: SkillPackKind;
  source: string;
  projectId: string;
  assetId: string;
  scope: KnowScope;
  status: SkillPackStatus;
  st: KnowSt;
  immutable: boolean;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface SkillPackDetail {
  pack: SkillPack;
  bodyMd: string;
}

// ── 后端原始行 ──
interface RawAsset {
  asset_id?: string; project_id?: string;
  class?: string; class_zh?: string; category?: string;
  title?: string; one_liner?: string; applies_when?: string;
  status?: string; view_state?: string;
  needs_review?: boolean; retire_suggested?: boolean; conflict_with?: string[];
  scope?: string; scope_set_by?: string;
  utility?: number; use_count?: number; cited_ok?: number; cited_bad?: number;
  source_count?: number;
  created_at?: string | null; updated_at?: string | null;
  evidence?: {
    source_ref?: string; node_id?: string; source_kind?: string; step?: number | null;
    observed_at?: string | null; excerpt?: string; agent_id?: string | null;
  }[];
  body_md?: string;
  usage_events?: { kind?: string; step?: number | null; at?: string | null }[];
}

interface RawSkillPack {
  pack_id?: string; name?: string; description?: string;
  kind?: string; source_kind?: string; source?: string;
  project_id?: string; asset_id?: string; scope?: string;
  status?: string; view_state?: string; immutable?: boolean;
  created_at?: string | null; updated_at?: string | null;
}

// ═══════════════════════════════════════════════════════════════
// 映射
// ═══════════════════════════════════════════════════════════════

export function chipOf(cat: KnowCat): KnowChip {
  switch (cat) {
    case '约定': return 'info';
    case '坑': return 'warn';
    case '清单': return 'ok';
    default: return 'acc';            // 模式
  }
}

const CATS_SET = new Set<string>(['约定', '坑', '模式', '清单']);

function toCard(raw: RawAsset): KnowCard | null {
  const assetId = String(raw.asset_id || '');
  const projectId = String(raw.project_id || '');
  if (!assetId || !projectId) return null;
  const cat: KnowCat = CATS_SET.has(String(raw.category || ''))
    ? (raw.category as KnowCat) : '模式';
  const status = ((): AssetStatus => {
    const s = String(raw.status || 'candidate');
    return (['seed', 'candidate', 'validated', 'core', 'retired'] as const)
      .includes(s as AssetStatus) ? (s as AssetStatus) : 'candidate';
  })();
  const st: KnowSt = raw.view_state === 'pending' ? 'pending'
    : raw.view_state === 'retired' ? 'retired' : 'ok';
  return {
    id: `${projectId}:${assetId}`,
    assetId,
    projectId,
    cls: String(raw.class || ''),
    clsZh: String(raw.class_zh || ''),
    cat,
    chip: chipOf(cat),
    title: String(raw.title || i18n.t('knowledge.05')),
    body: String(raw.one_liner || ''),
    appliesWhen: String(raw.applies_when || ''),
    status,
    st,
    needsReview: !!raw.needs_review,
    retireSuggested: !!raw.retire_suggested,
    conflictWith: (raw.conflict_with || []).map(String),
    scope: raw.scope === 'global' ? 'global' : 'project',
    scopeSetBy: raw.scope_set_by === 'user' ? 'user' : 'system',
    utility: Number(raw.utility || 0),
    cites: Math.max(0, Number(raw.use_count || 0)),
    citedOk: Math.max(0, Number(raw.cited_ok || 0)),
    citedBad: Math.max(0, Number(raw.cited_bad || 0)),
    sourceCount: Math.max(0, Number(raw.source_count || 0)),
    firstSeen: raw.created_at ?? null,
    lastSeen: raw.updated_at ?? null,
    evidence: (raw.evidence || []).map((ev) => ({
      sourceRef: String(ev.source_ref || ''),
      nodeId: String(ev.node_id || ''),
      sourceKind: String(ev.source_kind || ''),
      step: typeof ev.step === 'number' ? ev.step : null,
      observedAt: ev.observed_at ?? null,
      excerpt: String(ev.excerpt || ''),
      agentId: ev.agent_id ? String(ev.agent_id) : null,
    })),
  };
}

function toSkillPack(raw: RawSkillPack): SkillPack | null {
  const packId = String(raw.pack_id || '');
  if (!packId) return null;
  const kindRaw = String(raw.kind || raw.source_kind || 'third_party');
  const kind: SkillPackKind = kindRaw === 'system_builtin'
    ? 'system_builtin'
    : kindRaw === 'project_experience' ? 'project_experience' : 'third_party';
  const statusRaw = String(raw.status || (kind === 'project_experience' ? 'pending' : 'active'));
  const status: SkillPackStatus = statusRaw === 'active'
    ? 'active' : statusRaw === 'retired' ? 'retired' : 'pending';
  return {
    packId,
    name: String(raw.name || i18n.t('knowledge.06')),
    description: String(raw.description || ''),
    kind,
    source: String(raw.source || ''),
    projectId: String(raw.project_id || ''),
    assetId: String(raw.asset_id || ''),
    scope: raw.scope === 'project' ? 'project' : 'global',
    status,
    st: status === 'active' ? 'ok' : status,
    immutable: kind === 'system_builtin' || !!raw.immutable,
    createdAt: raw.created_at ?? null,
    updatedAt: raw.updated_at ?? null,
  };
}

/** 「沉淀于 …」：与收藏页同一口味的相对时间 */
export function knowDateLabel(iso: string | null): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const diff = Date.now() - t;
  const min = 60_000;
  if (diff < 2 * min) return i18n.t('favorites.03');
  if (diff < 60 * min) return i18n.t('common.minutesAgo', { n: Math.floor(diff / min) });
  if (diff < 24 * 60 * min) return i18n.t('common.hoursAgo', { n: Math.floor(diff / (60 * min)) });
  if (diff < 48 * 60 * min) return i18n.t('common.17');
  if (diff < 30 * 24 * 60 * min) return i18n.t('common.daysAgo', { n: Math.floor(diff / (24 * 60 * min)) });
  const d = new Date(t);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** 来源文件名（"handoffs/01-认证/report-05.md" → "report-05"；审批的点前缀剥掉） */
export function sourceLabel(ref: string): string {
  const name = ref.split('/').pop() || ref;
  return name.replace(/\.md$/i, '').replace(/^\./, '') || i18n.t('knowledge.view.01');
}

/** 使用信号的人话（引用历史弹窗用） */
export const USAGE_ZH: Record<string, string> = {
  used_and_approved: 'knowledge.03',
  used_and_rejected: 'knowledge.04',
  declared_not_helpful: 'knowledge.01',
  matched_never_used: 'knowledge.02',
};

// ═══════════════════════════════════════════════════════════════
// API 客户端
// ═══════════════════════════════════════════════════════════════

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), 8000);
  try {
    const resp = await runtimeFetch(`${runtimeHttpBase()}/api/knowledge${path}`, {
      ...init, signal: ctrl.signal,
    });
    const data = (await resp.json()) as T & { ok?: boolean; error?: string };
    if (!resp.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    return data;
  } finally {
    window.clearTimeout(timer);
  }
}

export interface AssetOverridePatch {
  /** 重命名：只改标题——内容不可编辑（重构需求 2①） */
  title?: string;
  /** 调整范围·二级：全局/项目（harness 复用方向实时跟着走） */
  scope?: KnowScope;
  /** 调整范围·三级：约定/坑/模式/清单（价值判断实时跟着走） */
  category?: KnowCat;
  /** 停用不删除 / 恢复启用 */
  status?: 'retired' | 'active';
}

// ═══════════════════════════════════════════════════════════════
// Store
// ═══════════════════════════════════════════════════════════════

/** 右侧预览面板（跟 v0.36 文件预览同款交互：浮在右侧、可拖宽、Esc 关） */
export interface KnowPreviewState {
  kind: 'asset' | 'profile' | 'skill';
  /** kind==='asset' 时有效 */
  card: KnowCard | null;
  /** kind==='skill' 时有效 */
  pack?: SkillPack | null;
  /** profile 预览属于哪个项目 */
  projectId: string;
}

interface KnowledgeStore {
  loading: boolean;
  /** 0 = 还没成功拿到过数据 */
  loadedAt: number;
  error: string | null;
  cards: KnowCard[];
  skillpacks: {
    systemBuiltin: SkillPack[];
    projectExperience: SkillPack[];
    thirdParty: SkillPack[];
  };
  /** 全局知识存在画像文件的项目（「偏好画像」入口据此点亮） */
  profileProjects: string[];

  preview: KnowPreviewState | null;
  previewWidth: number;

  load: (opts?: { silent?: boolean }) => Promise<void>;
  loadSkillpacks: () => Promise<void>;
  reviewSkillpack: (pack: SkillPack, action: 'approve' | 'reject') => Promise<boolean>;
  setSkillpackStatus: (pack: SkillPack, status: SkillPackStatus) => Promise<boolean>;
  purgeSkillpack: (pack: SkillPack) => Promise<boolean>;
  fetchSkillpackDetail: (pack: SkillPack) => Promise<SkillPackDetail | null>;
  override: (card: KnowCard, patch: AssetOverridePatch) => Promise<boolean>;
  review: (card: KnowCard, action: 'approve' | 'reject') => Promise<boolean>;
  purge: (card: KnowCard) => Promise<boolean>;
  fetchDetail: (card: KnowCard) => Promise<KnowAssetDetail | null>;
  fetchProfile: (projectId: string) => Promise<string | null>;

  openPreview: (p: KnowPreviewState) => void;
  closePreview: () => void;
  setPreviewWidth: (px: number) => void;
}

export const KN_PREVIEW_MIN = 320;
export function knPreviewMax(): number {
  return Math.max(KN_PREVIEW_MIN, Math.floor(window.innerWidth * 0.62));
}
const clampW = (px: number): number =>
  Math.min(knPreviewMax(), Math.max(KN_PREVIEW_MIN, Math.round(Number(px) || 0)));

export const useKnowledgeStore = create<KnowledgeStore>()((set, get) => ({
  loading: false,
  loadedAt: 0,
  error: null,
  cards: [],
  skillpacks: { systemBuiltin: [], projectExperience: [], thirdParty: [] },
  profileProjects: [],
  preview: null,
  previewWidth: 420,

  async load(opts) {
    const silent = !!opts?.silent;
    if (!silent) set({ loading: true, error: null });
    try {
      const data = await apiFetch<{
        assets: RawAsset[];
        projects: { project_id?: string; profile_exists?: boolean }[];
      }>('/assets');
      const cards = (data.assets || [])
        .map(toCard)
        .filter((c): c is KnowCard => c !== null);
      const profileProjects = (data.projects || [])
        .filter((p) => p.profile_exists)
        .map((p) => String(p.project_id || ''))
        .filter(Boolean);
      set({ cards, profileProjects, loading: false, loadedAt: Date.now(), error: null });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 静默刷新失败：留着旧数据继续用，别把界面打回错误态。
      if (silent && get().loadedAt) {
        set({ loading: false });
      } else {
        set({ loading: false, error: msg });
      }
    }
  },

  async loadSkillpacks() {
    try {
      const data = await apiFetch<{
        system_builtin?: RawSkillPack[];
        project_experience?: RawSkillPack[];
        third_party?: RawSkillPack[];
      }>('/skillpacks');
      set({
        skillpacks: {
          systemBuiltin: (data.system_builtin || [])
            .map(toSkillPack).filter((p): p is SkillPack => p !== null),
          projectExperience: (data.project_experience || [])
            .map(toSkillPack).filter((p): p is SkillPack => p !== null),
          thirdParty: (data.third_party || [])
            .map(toSkillPack).filter((p): p is SkillPack => p !== null),
        },
      });
    } catch {
      /* 技能包取不到不打断知识图谱主视图；保留上一次成功数据。 */
    }
  },

  async reviewSkillpack(pack, action) {
    try {
      await apiFetch<{ ok: boolean }>(
        `/skillpacks/${encodeURIComponent(pack.packId)}/review`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        },
      );
      await get().loadSkillpacks();
      return true;
    } catch {
      await get().loadSkillpacks();
      return false;
    }
  },

  async setSkillpackStatus(pack, status) {
    try {
      await apiFetch<{ ok: boolean }>(
        `/skillpacks/${encodeURIComponent(pack.packId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status }),
        },
      );
      await get().loadSkillpacks();
      return true;
    } catch {
      await get().loadSkillpacks();
      return false;
    }
  },

  async purgeSkillpack(pack) {
    try {
      await apiFetch<{ ok: boolean }>(
        `/skillpacks/${encodeURIComponent(pack.packId)}/purge`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        },
      );
      set((s) => ({
        skillpacks: {
          systemBuiltin: s.skillpacks.systemBuiltin.filter((p) => p.packId !== pack.packId),
          projectExperience: s.skillpacks.projectExperience.filter((p) => p.packId !== pack.packId),
          thirdParty: s.skillpacks.thirdParty.filter((p) => p.packId !== pack.packId),
        },
        preview: s.preview?.kind === 'skill' && s.preview.pack?.packId === pack.packId
          ? null : s.preview,
      }));
      void get().loadSkillpacks();
      return true;
    } catch {
      await get().loadSkillpacks();
      return false;
    }
  },

  async fetchSkillpackDetail(pack) {
    try {
      const data = await apiFetch<{
        found: boolean; pack?: RawSkillPack; body_md?: string;
      }>(`/skillpacks/${encodeURIComponent(pack.packId)}`);
      if (!data.found || !data.pack) return null;
      const fresh = toSkillPack(data.pack);
      return { pack: fresh ?? pack, bodyMd: String(data.body_md || '') };
    } catch {
      return null;
    }
  },

  async override(card, patch) {
    // 乐观：先把本地卡片改成目标态，POST 失败由 load 重拉回滚。
    const optimistic = get().cards.map((c) => {
      if (c.id !== card.id) return c;
      const next: KnowCard = { ...c };
      if (patch.title) next.title = patch.title;
      if (patch.scope) { next.scope = patch.scope; next.scopeSetBy = 'user'; }
      if (patch.category) { next.cat = patch.category; next.chip = chipOf(patch.category); }
      if (patch.status === 'retired') { next.st = 'retired'; next.status = 'retired'; }
      if (patch.status === 'active') {
        next.st = 'ok';
        if (next.status === 'retired') next.status = 'validated';
      }
      return next;
    });
    set({ cards: optimistic });
    try {
      await apiFetch<{ ok: boolean }>(
        `/${encodeURIComponent(card.projectId)}/assets/${encodeURIComponent(card.assetId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        },
      );
      // 后端是真源（refresh 会重算 utility/category/skill 导出等派生态）→ 静默对齐。
      void get().load({ silent: true });
      return true;
    } catch {
      void get().load({ silent: true });   // 回滚到服务端真实状态
      return false;
    }
  },

  async review(card, action) {
    try {
      await apiFetch<{ ok: boolean }>(
        `/${encodeURIComponent(card.projectId)}/assets/${encodeURIComponent(card.assetId)}/review`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        },
      );
      void get().load({ silent: true });
      return true;
    } catch {
      void get().load({ silent: true });
      return false;
    }
  },

  async purge(card) {
    try {
      await apiFetch<{ ok: boolean }>(
        `/${encodeURIComponent(card.projectId)}/assets/${encodeURIComponent(card.assetId)}/purge`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        },
      );
      // 彻底删除：本地立刻移除；预览开着这张卡就顺手关掉。
      set((s) => ({
        cards: s.cards.filter((c) => c.id !== card.id),
        preview: s.preview?.card?.id === card.id ? null : s.preview,
      }));
      void get().load({ silent: true });
      return true;
    } catch {
      void get().load({ silent: true });
      return false;
    }
  },

  async fetchDetail(card) {
    try {
      const data = await apiFetch<{ found: boolean; asset?: RawAsset }>(
        `/${encodeURIComponent(card.projectId)}/assets/${encodeURIComponent(card.assetId)}`,
      );
      if (!data.found || !data.asset) return null;
      const fresh = toCard({ ...data.asset, project_id: card.projectId });
      return {
        card: fresh ?? card,
        bodyMd: String(data.asset.body_md || ''),
        usage: (data.asset.usage_events || []).map((e) => ({
          kind: String(e.kind || ''),
          step: typeof e.step === 'number' ? e.step : null,
          at: e.at ?? null,
        })).reverse(),                     // 新在前
      };
    } catch {
      return null;
    }
  },

  async fetchProfile(projectId) {
    try {
      const data = await apiFetch<{ text: string }>(
        `/${encodeURIComponent(projectId)}/profile`,
      );
      return String(data.text || '');
    } catch {
      return null;
    }
  },

  openPreview(p) { set({ preview: p }); },
  closePreview() { set({ preview: null }); },
  setPreviewWidth(px) { set({ previewWidth: clampW(px) }); },
}));

export default useKnowledgeStore;
