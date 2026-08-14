/**
 * TokenUsagePanel.m4.test.tsx — [v1.0.34-M4] 上下文卡渲染 + StatCard 数字适配。
 *
 * 验证：
 *   1. 上下文卡有数据态：大数字占用率 + 右侧"自动压缩 N 次 · 节省 N 字符"
 *   2. 上下文卡数据缺失态：占用率 "--"，右侧空态文案
 *   3. 长数字（10 位以上）不溢出（StatCard 位数自适应：字号随位数缩小）
 *   4. 旧数据（无新字段）渲染不崩
 *
 * 环境：jsdom。面板依赖 socket store（openPanel 需要 sendCommand），
 * 用 bindTokenUsageSocket 注入假 socket + handleTokenUsageEvent 喂数据。
 */
// @vitest-environment jsdom

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import '../i18n';
import {
  useTokenUsageStore,
  handleTokenUsageEvent,
  bindTokenUsageSocket,
  _resetTokenUsageSerial,
} from '../store/tokenUsage';
import { TokenUsagePanel } from './TokenUsagePanel';

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
// useCountUp 依赖 matchMedia（reduced-motion 判定），jsdom 无此 API
(globalThis as unknown as { matchMedia: unknown }).matchMedia = ((): ((query: string) => { matches: boolean; addEventListener(): void; removeEventListener(): void }) => {
  const stub = (): { matches: boolean; addEventListener(): void; removeEventListener(): void } => ({
    matches: false,
    addEventListener(): void {},
    removeEventListener(): void {},
  });
  stub.matches = false;
  return stub;
})();

function totalsPayload(totals: Record<string, unknown>): Record<string, unknown> {
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
      ...totals,
    },
  };
}

function openPanelWithData(totals: Record<string, unknown>): void {
  const socket = { sendCommand: () => true } as never;
  bindTokenUsageSocket(socket);
  useTokenUsageStore.getState().openPanel('p1', { kind: '7d' });
  handleTokenUsageEvent(totalsPayload(totals));
  bindTokenUsageSocket(null);
}

describe('TokenUsagePanel · M4 上下文占用卡', () => {
  beforeEach(() => {
    _resetTokenUsageSerial();
    useTokenUsageStore.setState({ open: false, projectId: null, data: null, range: { kind: '7d' } });
  });

  it('有数据态：占用率大字 + 指标双行（本回合/累计）', () => {
    openPanelWithData({
      compression_count: 14,
      saved_chars: 138_789,
      compression_by_method: { fold_log: 2, compact_json: 12 },
      context_usage_pct: 51.24,
      latest_compression_count: 2,
      latest_saved_chars: 12_345,
      latest_projected_count: 8,
      projected_count: 156,
    });
    const { container } = render(<TokenUsagePanel />);
    // DOM 顺序第 4 张卡 = 上下文占用（右侧通栏）
    const cards = container.querySelectorAll('.tk-card');
    const card = cards[3];
    expect(card).toBeDefined();
    expect(card?.querySelector('.tk-context-label')?.textContent).toContain('AI 记忆用量');
    expect(card?.querySelector('.tk-context-value')?.textContent).toBe('51%');
    // 指标两行（纵向铺满）：自动压缩（名+解释 / 本回合·累计·省字符）/ 历史精简
    const metrics = Array.from(card?.querySelectorAll('.tk-context-metric') ?? []);
    expect(metrics.length).toBe(2);
    const row0 = metrics[0]?.querySelector('.tk-context-metric-row')?.textContent ?? '';
    expect(metrics[0]?.querySelector('.tk-context-metric-name')?.textContent).toContain('自动压缩');
    expect(row0).toContain('最近一轮');
    expect(row0).toContain('2');
    expect(row0).toContain('累计');
    expect(row0).toContain('14');
    // 省字符 → 约词元（138,789 / 1.6 ≈ 86,743 → 8.7万，标注「约」）
    expect(row0).toContain('约');
    expect(row0).toContain('8.7万');
    expect(row0).toContain('词元');
    const row1 = metrics[1]?.querySelector('.tk-context-metric-row')?.textContent ?? '';
    expect(metrics[1]?.querySelector('.tk-context-metric-name')?.textContent).toContain('历史对话已自动精简');
    expect(row1).toContain('8');
    expect(row1).toContain('156');
  });

  it('瞬时组缺失：本回合显示 --，累计正常', () => {
    openPanelWithData({
      compression_count: 5,
      saved_chars: 40_000,
      context_usage_pct: 40.0,
      // 无 latest_* 字段（旧后端数据）
    });
    const { container } = render(<TokenUsagePanel />);
    const card = container.querySelectorAll('.tk-card')[3];
    const metrics = Array.from(card?.querySelectorAll('.tk-context-metric') ?? []);
    const row0 = metrics[0]?.querySelector('.tk-context-metric-row')?.textContent ?? '';
    const row1 = metrics[1]?.querySelector('.tk-context-metric-row')?.textContent ?? '';
    expect(row0).toContain('--');
    expect(row0).toContain('5');
    expect(row1).toContain('--');
  });

  it('数据缺失态：占用率 "--" + 空态文案', () => {
    // 无任何用量/压缩/投影数据
    openPanelWithData({
      total_calls: 0,
      total_input: 0,
      total_output: 0,
      compression_count: 0,
      saved_chars: 0,
      context_usage_pct: null,
    });
    const { container } = render(<TokenUsagePanel />);
    const card = container.querySelectorAll('.tk-card')[3];
    expect(card?.querySelector('.tk-context-value')?.textContent).toBe('--');
    expect(card?.querySelector('.tk-context-empty')?.textContent).toBeTruthy();
  });

  it('长数字不溢出：StatCard 字号随位数缩小', () => {
    openPanelWithData({
      total_input: 100_000_000_000_000,
      total_output: 0,
      compression_count: 0,
      saved_chars: 0,
      context_usage_pct: null,
    });
    // jsdom 无布局引擎，clientWidth 恒 0 → 打桩让位数自适应计算生效
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: function get() { return 200; }, // 模拟统计卡可用宽度
    });
    const { container } = render(<TokenUsagePanel />);
    const values = Array.from(container.querySelectorAll('.tk-card-value'));
    // 100,000,000,000,000 → 千分位 "100,000,000,000,000"（15 位数字）
    const longValue = '100,000,000,000,000';
    const card = values.find((el) => el.textContent === longValue);
    expect(card).toBeDefined();
    if (card) {
      const fontSize = Number((card as HTMLElement).style.fontSize.replace('px', ''));
      expect(fontSize).toBeGreaterThanOrEqual(11);
      expect(fontSize).toBeLessThan(28);
      // 200px ÷ (15 位数字 × 0.62) ≈ 21.5 → floor 21 → 证明按位数计算而非固定 28
      expect(fontSize).toBe(21);
    }
  });

  it('旧数据渲染不崩（无 M4 字段）', () => {
    openPanelWithData({
      total_calls: 0,
      compression_count: 0,
      saved_chars: 0,
      compression_by_method: {},
      context_usage_pct: null,
    });
    const { container } = render(<TokenUsagePanel />);
    // 4 张卡都在（含上下文占用卡）
    expect(container.querySelectorAll('.tk-card').length).toBe(4);
    expect(container.querySelector('.tk-context-empty')).not.toBeNull();
  });

  it('面板关闭时不渲染', () => {
    const { container } = render(<TokenUsagePanel />);
    expect(container.querySelector('.tk-panel')).toBeNull();
  });
});
