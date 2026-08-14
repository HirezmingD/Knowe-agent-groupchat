/**
 * tokenUsage.m4.test.ts — [v1.0.34-M4] 上下文占用三数（前端 store 映射）。
 *
 * 验证 normalizePayload 对新字段的映射：
 *   1. compression_count / saved_chars / compression_by_method 正确透传
 *   2. context_usage_pct 合法值透传、越界/缺失 -> null
 *   3. 旧数据（无新字段）不崩，默认 0 / null
 *   4. handleTokenUsageEvent 端到端：响应帧 -> store.data 三数就位
 */
// @vitest-environment jsdom

import { describe, it, expect, beforeEach } from 'vitest';
import {
  useTokenUsageStore,
  handleTokenUsageEvent,
  bindTokenUsageSocket,
  _resetTokenUsageSerial,
} from './tokenUsage';

function payload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'token_usage_res',
    project_id: 'p1',
    request_id: 1,
    current_model: 'deepseek-chat',
    daily: [],
    by_agent: [],
    by_model: [],
    totals: {
      total_input: 100,
      total_output: 20,
      total_calls: 3,
      cache_hit_input: 60,
      cache_miss_input: 40,
      estimated_cost_cny: 0.01,
      estimated_cost_usd: null,
      compression_count: 3,
      saved_chars: 45000,
      compression_by_method: { fold_log: 2, compact_json: 1 },
      context_usage_pct: 62.5,
    },
    ...overrides,
  };
}

/** 打开 p1 面板并注入一条响应（等价于「面板开着、后端回了数据」）。 */
function openAndHandle(event: Record<string, unknown>): void {
  const socket = { sendCommand: () => true } as never;
  bindTokenUsageSocket(socket);
  useTokenUsageStore.getState().openPanel('p1', { kind: '7d' });
  handleTokenUsageEvent(event);
  bindTokenUsageSocket(null);
}

describe('tokenUsage · M4 三数映射', () => {
  beforeEach(() => {
    _resetTokenUsageSerial();
    useTokenUsageStore.setState({ open: false, projectId: null, data: null, range: { kind: '7d' } });
  });

  it('normalizePayload 透传三数', () => {
    openAndHandle(payload());
    const totals = useTokenUsageStore.getState().data?.totals;
    expect(totals?.compression_count).toBe(3);
    expect(totals?.saved_chars).toBe(45000);
    expect(totals?.compression_by_method).toEqual({ fold_log: 2, compact_json: 1 });
    expect(totals?.context_usage_pct).toBe(62.5);
  });

  it('旧数据（无新字段）默认 0 / null，不崩', () => {
    openAndHandle(payload({ totals: {
      total_input: 100,
      total_output: 20,
      total_calls: 1,
      cache_hit_input: 0,
      cache_miss_input: 100,
      estimated_cost_cny: null,
      estimated_cost_usd: null,
    } }));
    const totals = useTokenUsageStore.getState().data?.totals;
    expect(totals?.compression_count).toBe(0);
    expect(totals?.saved_chars).toBe(0);
    expect(totals?.compression_by_method).toEqual({});
    expect(totals?.context_usage_pct).toBeNull();
  });

  it('context_usage_pct 越界值 -> null（不做信任传递）', () => {
    openAndHandle(payload({ totals: {
      total_calls: 1,
      total_input: 10,
      total_output: 10,
      context_usage_pct: 250,
    } }));
    expect(useTokenUsageStore.getState().data?.totals.context_usage_pct).toBeNull();
  });

  it('compression_count 非法类型 -> 0', () => {
    openAndHandle(payload({ totals: {
      total_calls: 1,
      total_input: 10,
      total_output: 10,
      compression_count: 'abc',
      saved_chars: -5,
    } }));
    const totals = useTokenUsageStore.getState().data?.totals;
    expect(totals?.compression_count).toBe(0);
    expect(totals?.saved_chars).toBe(0);
  });

  it('openPanel 后旧响应（request_id 失配）被丢弃，不污染新数据', () => {
    const socket = { sendCommand: () => true } as never;
    bindTokenUsageSocket(socket);
    useTokenUsageStore.getState().openPanel('p1', { kind: '7d' });
    // 迟到的旧响应（request_id=999，与当前序号 1 失配）应被丢弃
    const handled = handleTokenUsageEvent(payload({ request_id: 999, totals: {
      total_calls: 99,
      total_input: 999,
      total_output: 999,
    } }));
    expect(handled).toBe(true);
    expect(useTokenUsageStore.getState().data).toBeNull();
    bindTokenUsageSocket(null);
  });
});
