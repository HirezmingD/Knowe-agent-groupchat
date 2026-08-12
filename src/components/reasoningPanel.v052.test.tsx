import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { ReasoningPanel } from './ReasoningPanel';
import '../i18n';   // 初始化 react-i18next（测试环境 i18n 实例）

// @vitest-environment jsdom

// jsdom 缺失的浏览器 API 打桩（同 sessionHost.test.tsx）
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

/**
 * [v1.0.23.5_2] 折叠零 DOM 回归测试。
 *
 * 背景（审计 01/02）：折叠态（grid-template-rows:0fr）只是视觉隐藏，
 * 正文全文仍在 DOM 且参与布局——窗口 resize 时 3.7 万像素推理全文被全量重排
 * → 布局风暴 → 系统卡死。修复：折叠（expanded=false）不挂载正文。
 *
 * 本测试锁定三条红线：
 *   1. 落定（live=false）初始折叠 → .reasoning-scroll 不挂载（零 DOM）；
 *   2. 点击展开 → 正文挂载渲染；
 *   3. 流式（live=true）→ 面板始终挂载（不受修复影响）。
 */

// Markdown 是异步渲染组件，这里 stub 成同步 div，聚焦面板挂载行为本身。
vi.mock('./markdown', () => ({
  Markdown: ({ text }: { text: string }) => <div data-testid="md-para">{text}</div>,
}));

describe('ReasoningPanel [v1.0.23.5_2] 折叠零 DOM', () => {
  beforeEach(() => {
    cleanup();
  });
  afterEach(() => {
    cleanup();
  });

  const LONG = '第一段思考内容\n\n第二段思考内容\n\n第三段思考内容';

  it('落定（live=false）初始折叠：正文不挂载，零 DOM', () => {
    const { container } = render(<ReasoningPanel text={LONG} seconds={5} />);
    // trigger 行在（绿点 + 标题 + chevron）
    expect(screen.getByRole('button')).toBeTruthy();
    // 正文零 DOM
    expect(container.querySelector('.reasoning-scroll')).toBeNull();
    expect(container.querySelector('.reasoning-stream')).toBeNull();
    expect(screen.queryAllByTestId('md-para')).toHaveLength(0);
    expect(container.textContent).not.toContain('第一段思考内容');
  });

  it('点击展开 → 正文挂载渲染全文', () => {
    const { container } = render(<ReasoningPanel text={LONG} seconds={5} />);
    expect(container.querySelector('.reasoning-scroll')).toBeNull();

    fireEvent.click(screen.getByRole('button'));
    // 展开后正文挂载
    expect(container.querySelector('.reasoning-scroll')).not.toBeNull();
    const paras = screen.queryAllByTestId('md-para');
    expect(paras).toHaveLength(3);
    expect(screen.getByText('第一段思考内容')).toBeTruthy();
  });

  it('再次点击折叠 → 正文卸载回零 DOM', () => {
    const { container } = render(<ReasoningPanel text={LONG} seconds={5} />);
    fireEvent.click(screen.getByRole('button'));
    expect(container.querySelector('.reasoning-scroll')).not.toBeNull();

    fireEvent.click(screen.getByRole('button'));
    expect(container.querySelector('.reasoning-scroll')).toBeNull();
    expect(screen.queryAllByTestId('md-para')).toHaveLength(0);
  });

  it('流式（live=true）面板始终挂载，不受修复影响', () => {
    const { container } = render(<ReasoningPanel text={LONG} live />);
    expect(container.querySelector('.reasoning-scroll')).not.toBeNull();
    expect(container.querySelector('.reasoning-scroll.live')).not.toBeNull();
    // 流式最后一段还在累积，不显示
    expect(screen.queryAllByTestId('md-para')).toHaveLength(2);
  });

  it('live 从 true 变 false：自动折叠 → 正文卸载', () => {
    const { container, rerender } = render(<ReasoningPanel text={LONG} live />);
    expect(container.querySelector('.reasoning-scroll')).not.toBeNull();

    // rerender 后 effect（live→false 自动折叠）在 act 内 flush
    rerender(<ReasoningPanel text={LONG} seconds={5} />);
    expect(container.querySelector('.reasoning-scroll')).toBeNull();
  });
});
