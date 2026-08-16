// src/store/tokenUsage.ts
// [v1.0.20.1-M3] 项目 Token 消耗抽屉的独立状态。
//
// Token 统计是请求/响应型旁路，不属于聊天时间线，也不参与项目 seq 水位。因此它像
// RecordsDrawer 一样使用独立 Zustand store；App 只负责注入全局唯一 Socket，并在统一
// onEvent 入口先把 token_usage_res 分流到这里。
//
// v1.0.20.1 变更：
// - 时间范围（range）由前端筛选栏持有，请求帧带 start_ts/end_ts（epoch 秒），
//   金额/三桶/by_model/by_agent 全部由后端按范围聚合（前端不本地过滤）。
// - 三桶（cache_hit_input / cache_miss_input / output）与双币金额
//   （estimated_cost_cny / estimated_cost_usd）随 M1/M2 后端协议落地。

import { create } from 'zustand';
import type { SocketAPI } from '../transport/socket';
import i18n from '../i18n';

export type TokenRangeKind = 'today' | '7d' | '30d' | 'total' | 'custom';

export interface TokenRange {
  kind: TokenRangeKind;
  /** 仅 kind === 'custom'：YYYY-MM-DD 起止（含两端）。 */
  start?: string;
  end?: string;
}

export interface TokenUsageDaily {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_hit_input: number;
  cache_miss_input: number;
  calls: number;
}

export interface TokenUsageTotals {
  total_input: number;
  total_output: number;
  total_tokens: number;
  total_calls: number;
  cache_hit_input: number;
  cache_miss_input: number;
  estimated_cost_cny: number | null;
  estimated_cost_usd: number | null;
  priced_cost_cny?: number;
  priced_cost_usd?: number;
  cost_complete?: boolean;
  unpriced_tokens?: number;
  // [v1.0.34-M4] 上下文占用三数
  compression_count: number;
  saved_chars: number;
  compression_by_method: Record<string, number>;
  /** 范围内最新一条记录的投影后估算 token ÷ 窗口；无数据为 null。 */
  context_usage_pct: number | null;
  // [v1.0.34-M4-v2] 瞬时组（本回合=范围内最新一条含数据的记录；无数据为 null）
  latest_compression_count: number | null;
  latest_saved_chars: number | null;
  latest_projected_count: number | null;
  // [v1.0.34-M4-v2] 投影保留条数累计
  projected_count: number;
}

export interface TokenUsageAgent {
  agent_id: string;
  role: string;
  name: string;
  total_input: number;
  total_output: number;
  total_tokens: number;
  cache_hit_input: number;
  cache_miss_input: number;
  calls?: number;
  /** [v1.0.20.3] 按 Agent 金额：该 agent 所有调用按 model 单价累加（CNY/USD）。 */
  estimated_cost_cny?: number | null;
  estimated_cost_usd?: number | null;
}

export interface TokenUsagePricing {
  model: string;
  known: boolean;
  input_cost_per_1M: number | null;
  output_cost_per_1M: number | null;
  cache_hit_input_cost_per_1M: number | null;
  currency: string;
  source: string;
}

export interface TokenUsageModel {
  provider: string;
  model: string;
  total_input: number;
  total_output: number;
  total_tokens: number;
  cache_hit_input: number;
  cache_miss_input: number;
  calls: number;
  estimated_cost_cny: number | null;
  estimated_cost_usd: number | null;
  pricing: TokenUsagePricing;
}

export interface TokenUsageData {
  project_id: string;
  daily: TokenUsageDaily[];
  totals: TokenUsageTotals;
  by_agent: TokenUsageAgent[];
  by_model: TokenUsageModel[];
  current_model: string;
  pricing: TokenUsagePricing;
}

type TokenUsageSocket = SocketAPI & {
  /** 新 transport 的通用控制帧出口。 */
  sendCommand?: (frame: Record<string, unknown>) => void;
};

interface TokenUsageState {
  open: boolean;
  projectId: string | null;
  range: TokenRange;
  data: TokenUsageData | null;
  loading: boolean;
  error: string | null;

  openPanel: (projectId: string, range?: TokenRange) => void;
  closePanel: () => void;
  retry: () => void;
}

const REQUEST_TIMEOUT_MS = 12_000;
let boundSocket: SocketAPI | null = null;
let requestTimer: ReturnType<typeof setTimeout> | null = null;
let requestSerial = 0;

function clearRequestTimer(): void {
  if (requestTimer) clearTimeout(requestTimer);
  requestTimer = null;
}

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function nonNegativeInt(value: unknown): number {
  const number = finiteNumber(value, 0);
  return number >= 0 ? Math.trunc(number) : 0;
}

/** [v1.0.34-M4-v2] 可空非负整数：null/缺失保留 null，非法值回退 null。 */
function nullableNonNegInt(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const number = finiteNumber(value, -1);
  return number >= 0 ? Math.trunc(number) : null;
}

function nullableCost(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function objectOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function normalizePricing(value: unknown, fallbackModel = ''): TokenUsagePricing {
  const row = objectOf(value);
  const source = typeof row.source === 'string' && row.source.trim()
    ? row.source.trim()
    : 'unknown';
  return {
    model: typeof row.model === 'string' ? row.model : fallbackModel,
    known: row.known === true,
    input_cost_per_1M: nullableCost(row.input_cost_per_1M),
    output_cost_per_1M: nullableCost(row.output_cost_per_1M),
    cache_hit_input_cost_per_1M: nullableCost(row.cache_hit_input_cost_per_1M),
    currency: typeof row.currency === 'string' ? row.currency : 'CNY',
    source,
  };
}

function normalizeDaily(value: unknown): TokenUsageDaily[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((candidate): TokenUsageDaily[] => {
    const row = objectOf(candidate);
    const date = typeof row.date === 'string' ? row.date.trim() : '';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return [];
    const input = nonNegativeInt(row.input_tokens);
    const output = nonNegativeInt(row.output_tokens);
    const hit = nonNegativeInt(row.cache_hit_input);
    const miss = nonNegativeInt(row.cache_miss_input);
    return [{
      date,
      input_tokens: input,
      output_tokens: output,
      total_tokens: input + output,
      cache_hit_input: hit,
      cache_miss_input: miss > 0 ? miss : Math.max(0, input - hit),
      calls: nonNegativeInt(row.calls),
    }];
  }).sort((a, b) => a.date.localeCompare(b.date));
}

function normalizeAgents(value: unknown): TokenUsageAgent[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((candidate): TokenUsageAgent[] => {
    const row = objectOf(candidate);
    const agentId = typeof row.agent_id === 'string' ? row.agent_id.trim() : '';
    if (!agentId) return [];
    const input = nonNegativeInt(row.total_input);
    const output = nonNegativeInt(row.total_output);
    const hit = nonNegativeInt(row.cache_hit_input);
    return [{
      agent_id: agentId,
      role: typeof row.role === 'string' ? row.role : '',
      name: typeof row.name === 'string' ? row.name : '',
      total_input: input,
      total_output: output,
      total_tokens: input + output,
      cache_hit_input: hit,
      cache_miss_input: nonNegativeInt(row.cache_miss_input),
      calls: nonNegativeInt(row.calls),
      // [v1.0.20.3] 按 Agent 金额：后端 by_agent 已带（model×单价×用量累加），
      // 之前映射漏透传导致前端永远 undefined → 显示「暂无价格表」。
      estimated_cost_cny: nullableCost(row.estimated_cost_cny),
      estimated_cost_usd: nullableCost(row.estimated_cost_usd),
    }];
  }).sort((a, b) => b.total_tokens - a.total_tokens || a.agent_id.localeCompare(b.agent_id));
}

function normalizeModels(value: unknown): TokenUsageModel[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((candidate): TokenUsageModel[] => {
    const row = objectOf(candidate);
    const model = typeof row.model === 'string' ? row.model.trim() : '';
    if (!model) return [];
    const input = nonNegativeInt(row.total_input);
    const output = nonNegativeInt(row.total_output);
    const hit = nonNegativeInt(row.cache_hit_input);
    return [{
      provider: typeof row.provider === 'string' ? row.provider.trim() : '',
      model,
      total_input: input,
      total_output: output,
      total_tokens: input + output,
      cache_hit_input: hit,
      cache_miss_input: nonNegativeInt(row.cache_miss_input),
      calls: nonNegativeInt(row.calls),
      estimated_cost_cny: nullableCost(row.estimated_cost_cny),
      estimated_cost_usd: nullableCost(row.estimated_cost_usd),
      pricing: normalizePricing(row.pricing, model),
    }];
  }).sort((a, b) => b.total_tokens - a.total_tokens || a.model.localeCompare(b.model));
}

function normalizePayload(event: Record<string, unknown>): TokenUsageData {
  const totals = objectOf(event.totals);
  const totalInput = nonNegativeInt(totals.total_input);
  const totalOutput = nonNegativeInt(totals.total_output);
  const currentModel = typeof event.current_model === 'string' ? event.current_model.trim() : '';
  const compressionByMethod: Record<string, number> = {};
  const rawByMethod = objectOf(totals.compression_by_method);
  for (const [method, count] of Object.entries(rawByMethod)) {
    compressionByMethod[method] = nonNegativeInt(count);
  }
  const rawPct = totals.context_usage_pct;
  const contextPct = typeof rawPct === 'number' && Number.isFinite(rawPct) && rawPct >= 0 && rawPct <= 100
    ? rawPct
    : null;
  return {
    project_id: typeof event.project_id === 'string' ? event.project_id : '',
    daily: normalizeDaily(event.daily),
    totals: {
      total_input: totalInput,
      total_output: totalOutput,
      total_tokens: totalInput + totalOutput,
      total_calls: nonNegativeInt(totals.total_calls),
      cache_hit_input: nonNegativeInt(totals.cache_hit_input),
      cache_miss_input: nonNegativeInt(totals.cache_miss_input),
      estimated_cost_cny: nullableCost(totals.estimated_cost_cny),
      estimated_cost_usd: nullableCost(totals.estimated_cost_usd),
      priced_cost_cny: nullableCost(totals.priced_cost_cny) ?? undefined,
      priced_cost_usd: nullableCost(totals.priced_cost_usd) ?? undefined,
      cost_complete: typeof totals.cost_complete === 'boolean' ? totals.cost_complete : undefined,
      unpriced_tokens: nonNegativeInt(totals.unpriced_tokens),
      // [v1.0.34-M4]
      compression_count: nonNegativeInt(totals.compression_count),
      saved_chars: nonNegativeInt(totals.saved_chars),
      compression_by_method: compressionByMethod,
      context_usage_pct: contextPct,
      // [v1.0.34-M4-v2] 瞬时组 + 投影累计
      latest_compression_count: nullableNonNegInt(totals.latest_compression_count),
      latest_saved_chars: nullableNonNegInt(totals.latest_saved_chars),
      latest_projected_count: nullableNonNegInt(totals.latest_projected_count),
      projected_count: nonNegativeInt(totals.projected_count),
    },
    by_agent: normalizeAgents(event.by_agent),
    by_model: normalizeModels(event.by_model),
    current_model: currentModel,
    pricing: normalizePricing(event.pricing, currentModel),
  };
}

/** 时间范围 → 后端 epoch 秒边界（含两端）。总计 = 无界。 */
export function rangeToBounds(range: TokenRange): { start_ts?: number; end_ts?: number } {
  if (range.kind === 'total') return {};
  const now = new Date();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  if (range.kind === 'today') {
    return { start_ts: Math.floor(start.getTime() / 1000) };
  }
  if (range.kind === '7d') {
    start.setDate(start.getDate() - 6);
    return { start_ts: Math.floor(start.getTime() / 1000) };
  }
  if (range.kind === '30d') {
    start.setDate(start.getDate() - 29);
    return { start_ts: Math.floor(start.getTime() / 1000) };
  }
  // custom：起止两端都要，end 覆盖到 23:59:59。
  const [sy, sm, sd] = (range.start || '').split('-').map(Number);
  const [ey, em, ed] = (range.end || '').split('-').map(Number);
  if (!sy || !sm || !sd || !ey || !em || !ed) return {};
  const startDate = new Date(sy, sm - 1, sd, 0, 0, 0, 0);
  const endDate = new Date(ey, em - 1, ed, 23, 59, 59, 999);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) return {};
  if (startDate.getTime() > endDate.getTime()) return {};
  return {
    start_ts: Math.floor(startDate.getTime() / 1000),
    end_ts: Math.floor(endDate.getTime() / 1000),
  };
}

function sendRequest(projectId: string, requestId: number, range: TokenRange): boolean {
  if (!boundSocket) return false;
  const socket = boundSocket as TokenUsageSocket;
  try {
    if (typeof socket.sendCommand === 'function') {
      socket.sendCommand({
        type: 'token_usage_req',
        project_id: projectId,
        request_id: requestId,
        ...rangeToBounds(range),
      });
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function beginRequest(projectId: string, range: TokenRange): void {
  clearRequestTimer();
  const serial = ++requestSerial;
  if (!sendRequest(projectId, serial, range)) {
    useTokenUsageStore.setState({
      loading: false,
      error: i18n.t('token.usage.02'),
    });
    return;
  }
  requestTimer = setTimeout(() => {
    const state = useTokenUsageStore.getState();
    if (serial !== requestSerial || !state.open || state.projectId !== projectId) return;
    useTokenUsageStore.setState({ loading: false, error: i18n.t('token.usage.01') });
  }, REQUEST_TIMEOUT_MS);
}

const DEFAULT_RANGE: TokenRange = { kind: '7d' };

export const useTokenUsageStore = create<TokenUsageState>((set, get) => ({
  open: false,
  projectId: null,
  range: DEFAULT_RANGE,
  data: null,
  loading: false,
  error: null,

  openPanel(projectId, range): void {
    const id = projectId.trim();
    if (!id) return;
    const previous = get();
    const nextRange = range ?? previous.range;
    const sameProject = previous.projectId === id;
    const sameRange = previous.range.kind === nextRange.kind
      && previous.range.start === nextRange.start
      && previous.range.end === nextRange.end;
    set({
      open: true,
      projectId: id,
      range: nextRange,
      // 同项目同范围：保留已有数据（避免每次点开都闪加载）；否则清空等新响应。
      data: sameProject && sameRange ? previous.data : null,
      loading: true,
      error: null,
    });
    beginRequest(id, nextRange);
  },

  closePanel(): void {
    clearRequestTimer();
    requestSerial += 1;
    set({ open: false, loading: false, error: null });
  },

  retry(): void {
    const { projectId, open, range } = get();
    if (!open || !projectId) return;
    set({ loading: true, error: null });
    beginRequest(projectId, range);
  },
}));

/** App.tsx 注入应用级唯一 socket；断开时清掉引用，绝不自己创建第二条连接。 */
export function bindTokenUsageSocket(socket: SocketAPI | null): void {
  boundSocket = socket;
  if (!socket) {
    clearRequestTimer();
    return;
  }
  const state = useTokenUsageStore.getState();
  if (state.open && state.projectId && state.error) state.retry();
}

/** 测试钩子：重置请求序号与计时器（vitest 用例共享模块实例，防序号跨用例累积）。 */
export function _resetTokenUsageSerial(): void {
  requestSerial = 0;
  clearRequestTimer();
}

/**
 * 从 App 的统一 onEvent 入口消费 token_usage_res。
 * 返回 true 表示该帧已被旁路处理，不能再进入聊天状态机或产生未读/时间线事件。
 */
export function handleTokenUsageEvent(value: unknown): boolean {
  const event = objectOf(value);
  if (event.type !== 'token_usage_res') return false;

  const state = useTokenUsageStore.getState();
  const projectId = typeof event.project_id === 'string' ? event.project_id : '';
  const responseRequestId = typeof event.request_id === 'number'
    && Number.isSafeInteger(event.request_id) && event.request_id > 0
    ? event.request_id
    : null;
  // 已关闭、已切项目，或同项目重试后旧响应才晚到：只丢弃，不复活/回退抽屉。
  if (!state.open || !state.projectId || projectId !== state.projectId) return true;
  if (responseRequestId != null && responseRequestId !== requestSerial) return true;

  clearRequestTimer();
  requestSerial += 1;
  if (typeof event.error === 'string' && event.error.trim()) {
    useTokenUsageStore.setState({ loading: false, error: event.error.trim() });
    return true;
  }

  useTokenUsageStore.setState({
    data: normalizePayload(event),
    loading: false,
    error: null,
  });
  return true;
}
