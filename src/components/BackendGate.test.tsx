/**
 * BackendGate.test.tsx — 后端状态指示器。
 *
 * 最要紧的一条：**浏览器里（没有 window.knowe）必须渲染为空且不报错。**
 * 前端能在浏览器单跑，是联调时最常用的姿势；这个组件要是在那儿炸了，
 * 整个界面就白了——一个「状态指示器」把应用干掉，说不过去。
 */


import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import BackendGate from './BackendGate';
import type { BackendPhase, BackendStatus, KnoweBridge } from '../shared/bridge';

// ═══════════════════════════════════════════════════════════════
// 假桥（模拟 Electron 主进程那一侧）
// ═══════════════════════════════════════════════════════════════

function status(phase: BackendPhase, message = ''): BackendStatus {
  return { phase, message, pid: phase === 'ready' ? 123 : null, logTail: [] };
}

class FakeBridge implements KnoweBridge {
  readonly isElectron = true as const;
  readonly version = 'test';
  readonly runtimeEndpoints = {
    httpBase: 'http://127.0.0.1:8081',
    wsUrl: 'ws://127.0.0.1:8080',
  } as const;

  private listeners = new Set<(s: BackendStatus) => void>();
  restartCalls = 0;
  /** restartBackend 返回的 promise 由测试手动兑现——好观察「重试中…」那一帧 */
  restartResolve: ((s: BackendStatus) => void) | null = null;

  constructor(private current: BackendStatus) {}

  getBackendStatus = (): Promise<BackendStatus> => Promise.resolve(this.current);

  onBackendStatus = (cb: (s: BackendStatus) => void): (() => void) => {
    this.listeners.add(cb);
    return () => { this.listeners.delete(cb); };
  };

  restartBackend = (): Promise<BackendStatus> => {
    this.restartCalls += 1;
    return new Promise((resolve) => { this.restartResolve = resolve; });
  };

  /** 主进程推一条新状态 */
  push(s: BackendStatus): void {
    this.current = s;
    act(() => { for (const fn of this.listeners) fn(s); });
  }

  selectDirectory = (): Promise<string | null> => Promise.resolve(null);   // [v0.7 A0]
  openPath = (_dir: string): Promise<void> => Promise.resolve();            // [v0.39.3]

  get listenerCount(): number {
    return this.listeners.size;
  }
}

function install(bridge: FakeBridge | undefined): void {
  (window as unknown as { knowe?: KnoweBridge }).knowe = bridge;
}

beforeEach(() => install(undefined));
afterEach(() => {
  install(undefined);
  vi.restoreAllMocks();
});

// ═══════════════════════════════════════════════════════════════

describe('BackendGate · 浏览器兜底', () => {
  it('★ 没有 window.knowe（浏览器模式）→ 渲染为空，且不报错', () => {
    const spy = vi.spyOn(console, 'error');
    const { container } = render(<BackendGate />);

    expect(container).toBeEmptyDOMElement();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('BackendGate · 显示规则', () => {
  it('ready → 什么都不显示（后端好着呢，不打扰用户）', async () => {
    install(new FakeBridge(status('ready')));
    const { container } = render(<BackendGate />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('stopped → 什么都不显示（正在退出应用，别这时候吓人）', async () => {
    install(new FakeBridge(status('stopped')));
    const { container } = render(<BackendGate />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('starting → 一行灰字「后端启动中…」', async () => {
    install(new FakeBridge(status('starting')));
    render(<BackendGate />);

    expect(await screen.findByText('后端启动中…')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();  // 启动中不给重试按钮
  });

  it.each<BackendPhase>(['crashed', 'failed'])(
    '%s → 「后端已断开」+ 重试按钮',
    async (phase) => {
      install(new FakeBridge(status(phase, '端口 8080 被占用')));
      render(<BackendGate />);

      expect(await screen.findByText('后端已断开')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '重试' })).toBeEnabled();
      // 出事的时候，「为什么」要写在脸上，别让人去翻日志
      expect(screen.getByText('端口 8080 被占用')).toBeInTheDocument();
    },
  );
});

describe('BackendGate · 重试', () => {
  it('★ 点重试 → 调 restartBackend，按钮变「重试中…」且禁用', async () => {
    const bridge = new FakeBridge(status('crashed', '进程退出'));
    install(bridge);
    render(<BackendGate />);

    await userEvent.click(await screen.findByRole('button', { name: '重试' }));

    expect(bridge.restartCalls).toBe(1);
    const btn = screen.getByRole('button');
    expect(btn).toHaveTextContent('重试中…');
    expect(btn).toBeDisabled();                    // 禁用 = 防连点，别把后端连着起五次
  });

  it('★ 后端恢复（推 ready）→ 提示整个消失', async () => {
    const bridge = new FakeBridge(status('crashed'));
    install(bridge);
    const { container } = render(<BackendGate />);

    await userEvent.click(await screen.findByRole('button', { name: '重试' }));
    bridge.push(status('ready'));

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('重启又失败（推 failed）→ 按钮回到可点状态，不许卡在「重试中…」', async () => {
    const bridge = new FakeBridge(status('crashed'));
    install(bridge);
    render(<BackendGate />);

    await userEvent.click(await screen.findByRole('button', { name: '重试' }));
    expect(screen.getByRole('button')).toBeDisabled();

    // 主进程回了一个 failed：restartBackend 的 promise 兑现为 failed
    act(() => bridge.restartResolve?.(status('failed', '还是起不来')));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '重试' })).toBeEnabled();
    });
    expect(screen.getByText('还是起不来')).toBeInTheDocument();
  });
});

describe('BackendGate · 状态推送', () => {
  it('starting → ready → crashed，界面跟着变', async () => {
    const bridge = new FakeBridge(status('starting'));
    install(bridge);
    const { container } = render(<BackendGate />);

    expect(await screen.findByText('后端启动中…')).toBeInTheDocument();

    bridge.push(status('ready'));
    await waitFor(() => expect(container).toBeEmptyDOMElement());

    bridge.push(status('crashed', '进程退出 code=1'));
    expect(await screen.findByText('后端已断开')).toBeInTheDocument();
  });

  it('★ 卸载时退订（不退订就是内存泄漏）', async () => {
    const bridge = new FakeBridge(status('crashed'));
    install(bridge);
    const { unmount } = render(<BackendGate />);

    await screen.findByText('后端已断开');
    expect(bridge.listenerCount).toBe(1);

    unmount();
    expect(bridge.listenerCount).toBe(0);
  });
});
