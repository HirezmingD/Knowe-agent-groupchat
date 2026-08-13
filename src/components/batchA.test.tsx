/**
 * batchA.test.tsx — 批次 A：ConnBadge / Rail / ConvList
 *
 * 每条测试都对应一个「和洲能在屏幕上看到」的事实。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { useKnoweStore } from '../store/store';
import type { ConnStatus } from '../store/state';
import { resetStore, seedConv, activate, installSocketSpy } from './__testkit';

import ConnBadge from './ConnBadge';
import Rail from './Rail';
import ConvList from './ConvList';

beforeEach(() => { cleanup(); resetStore(); });

// ═══════════════════════════════════════════════════════════════
describe('ConnBadge · 六态', () => {
  const CASES: [ConnStatus, string, string][] = [
    ['live', '已连接', 'ok'],
    ['reconnecting', '重连中', 'warn'],
    ['connecting', '连接中', ''],
    ['handshaking', '握手中', ''],
    ['resync', '重新同步', ''],
    ['closed', '未连接', ''],
  ];

  it.each(CASES)('conn=%s → 显示「%s」，色调 %s', (conn, text, tone) => {
    useKnoweStore.setState({ conn });
    const { container } = render(<ConnBadge />);
    expect(screen.getByText(text)).toBeTruthy();

    const badge = container.querySelector('.conn-badge');
    expect(badge).toBeTruthy();
    expect(badge?.classList.contains('ok')).toBe(tone === 'ok');
    expect(badge?.classList.contains('warn')).toBe(tone === 'warn');
  });

  it('状态变化 → 徽章跟着变（不需要刷新页面）', () => {
    useKnoweStore.setState({ conn: 'closed' as ConnStatus });
    render(<ConnBadge />);
    expect(screen.getByText('未连接')).toBeTruthy();

    act(() => { useKnoweStore.setState({ conn: 'live' as ConnStatus }); });
    expect(screen.getByText('已连接')).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════
describe('Rail · 视图切换', () => {
  it('默认高亮「项目」', () => {
    const { container } = render(<Rail />);
    const active = container.querySelector('.rail-btn.active');
    expect(active?.getAttribute('data-view')).toBe('chats');
  });

  it('点「联系人」→ store.activeView 变，高亮转移', () => {
    const { container } = render(<Rail />);
    fireEvent.click(screen.getByLabelText('联系人'));

    expect(useKnoweStore.getState().activeView).toBe('contacts');
    const active = container.querySelector('.rail-btn.active');
    expect(active?.getAttribute('data-view')).toBe('contacts');
  });
});

// ═══════════════════════════════════════════════════════════════
describe('ConvList · 项目列表', () => {
  it('store 里的项目 → 左栏逐条列出', () => {
    seedConv('p1', { name: '官网改版' });
    seedConv('p2', { name: '数据看板' });
    render(<ConvList />);

    expect(screen.getByText('官网改版')).toBeTruthy();
    expect(screen.getByText('数据看板')).toBeTruthy();
  });

  it('点项目 → switchProject，该项被标为 .active', () => {
    seedConv('p1', { name: '官网改版' });
    seedConv('p2', { name: '数据看板' });
    const { container } = render(<ConvList />);

    fireEvent.click(screen.getByText('数据看板'));
    expect(useKnoweStore.getState().activeProjectId).toBe('p2');

    const active = container.querySelector('.citem.active');
    expect(active?.getAttribute('data-conv')).toBe('p2');
  });

  it('有待确认审批 → 该项显示「待确认」徽标', () => {
    seedConv('p1', {
      name: '官网改版',
      items: [{
        kind: 'approval', cardId: 'c1', projectId: 'p1', tool: 'team',
        card: { status: 'pending_approval', expires_at: '', proposed: [] } as never,
        state: 'pending', expiresAt: '',
      }],
    });
    render(<ConvList />);
    expect(screen.getByText('待确认')).toBeTruthy();
  });

  it('全局搜索框筛出匹配项目', () => {
    seedConv('p1', { name: '官网改版' });
    seedConv('p2', { name: '数据看板' });
    render(<ConvList />);

    fireEvent.change(screen.getByLabelText('全局搜索'), { target: { value: '看板' } });
    expect(screen.queryByText('官网改版')).toBeNull();
    expect(screen.getByRole('option')).toHaveTextContent('数据看板');
  });

  it('＋ → 弹窗 → 创建 → 走 socket.createProject（前端先本地注册再发指令）', async () => {
    // [v0.7 A0] 弹窗里「项目目录」是必填的
    window.knowe = { selectDirectory: () => Promise.resolve('/test/dir'), version: 'test', isElectron: true } as never;

    const spy = installSocketSpy();
    render(<ConvList />);

    fireEvent.click(screen.getByLabelText('新建项目'));
    fireEvent.change(screen.getByLabelText('项目名'), { target: { value: '新官网' } });
    // 点「选择目录」→ resolve → 目录填入输入框
    fireEvent.click(screen.getByText('选择目录'));
    await vi.waitFor(() => expect(screen.getByLabelText('项目目录')).toHaveValue('/test/dir'));
    fireEvent.click(screen.getByText('创建'));

    expect(spy.created.length).toBe(1);
    expect(spy.created[0]?.name).toBe('新官网');
    // [v0.7 #5] createProject 现在本地注册 → 本地已有会话
    expect(useKnoweStore.getState().projectOrder.length).toBe(1);
  });
});

// 让 activate 在本文件被引用（避免 noUnusedLocals 报错）
void activate;
