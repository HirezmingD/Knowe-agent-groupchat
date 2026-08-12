/**
 * batchC.test.tsx — 批次 C：审批 / 花名册 / 轻提示
 *
 * 重点：审批「首个解决为准」——本地点按钮只禁用，状态翻转只认服务端事件。
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { useKnoweStore } from '../store/store';
import type { ApprovalCardData } from '../contract/envelope';
import { resetStore, member, installSocketSpy } from './__testkit';

import ApprovalCard from './ApprovalCard';
import RosterPanel from './RosterPanel';
import ToastHost from './ToastHost';

beforeEach(() => { cleanup(); resetStore(); });
afterEach(() => { vi.useRealTimers(); });

const teamCard = (expiresAt: string): ApprovalCardData => ({
  status: 'pending_approval',
  expires_at: expiresAt,
  proposed: [{ id: 'fe_1', role: '前端' }, { id: 'be_1', role: '后端' }],
}) as ApprovalCardData;

const taskCard = (expiresAt: string): ApprovalCardData => ({
  status: 'pending_approval',
  expires_at: expiresAt,
  target_id: 'fe_1',
  instruction: '把首页切出来',
}) as ApprovalCardData;

const inFuture = (sec: number): string => new Date(Date.now() + sec * 1000).toISOString();

// ═══════════════════════════════════════════════════════════════
describe('ApprovalCard · 团队卡', () => {
  const renderTeam = (state: 'pending' | 'confirmed' | 'rejected' | 'timeout' | 'cancelled' = 'pending') =>
    render(
      <ApprovalCard
        cardId="ap1" projectId="p1" tool="team"
        card={teamCard(inFuture(300))} state={state} expiresAt={inFuture(300)}
        members={[member('fe_1', '小前', '前端'), member('be_1', '小后', '后端')]}
      />,
    );

  it('未决 → 列出被提议的成员 + 确认/拒绝按钮 + 倒计时', () => {
    const { container } = renderTeam();
    expect(screen.getByText('小前')).toBeTruthy();
    expect(screen.getByText('小后')).toBeTruthy();
    expect(screen.getByText('确认')).toBeTruthy();
    expect(screen.getByText('拒绝')).toBeTruthy();
    expect(container.querySelector('.ap-count')?.textContent).toContain('剩余');
    expect(container.querySelector('.approval.settled')).toBeNull();
  });

  it('点「确认」→ 走 socket.approve，按钮立即禁用（防双击），但状态不本地翻转', () => {
    const spy = installSocketSpy();
    const { container } = renderTeam();

    fireEvent.click(screen.getByText('确认'));

    expect(spy.approved).toEqual([{ id: 'ap1', projectId: 'p1' }]);
    expect(screen.getByText('已提交…').hasAttribute('disabled')).toBe(true);
    expect((screen.getByText('拒绝') as HTMLButtonElement).disabled).toBe(true);
    // ★ 首个解决为准：卡片仍是未决态，等服务端 approval_resolved
    expect(container.querySelector('.approval.settled')).toBeNull();
  });

  it('点「拒绝」→ 走 socket.reject', () => {
    const spy = installSocketSpy();
    renderTeam();
    fireEvent.click(screen.getByText('拒绝'));
    expect(spy.rejected).toEqual([{ id: 'ap1', projectId: 'p1' }]);
  });

  it.each([
    ['confirmed', '已确认'],
    ['rejected', '已拒绝'],
    ['timeout', '已超时，提议自动撤回'],
    ['cancelled', '已取消'],
  ] as const)('终态 %s → .settled + 结果条「%s」，按钮消失', (state, text) => {
    const { container } = renderTeam(state);
    expect(container.querySelector('.approval.settled')).toBeTruthy();
    expect(container.querySelector('.ap-resolved-bar')).toBeTruthy();
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.queryByText('确认')).toBeNull();
  });

  it('倒计时归零 → 显示「已超时」且按钮禁用（等服务端落终态）', () => {
    vi.useFakeTimers();
    const past = new Date(Date.now() - 1000).toISOString();
    render(
      <ApprovalCard
        cardId="ap1" projectId="p1" tool="team"
        card={teamCard(past)} state="pending" expiresAt={past} members={[]}
      />,
    );
    act(() => { vi.advanceTimersByTime(1100); });

    expect(screen.getByText('已超时')).toBeTruthy();
    expect((screen.getByText('确认') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('ApprovalCard · 任务卡', () => {
  it('显示目标成员与任务正文', () => {
    const { container } = render(
      <ApprovalCard
        cardId="ap2" projectId="p1" tool="task"
        card={taskCard(inFuture(120))} state="pending" expiresAt={inFuture(120)}
        members={[member('fe_1', '小前', '前端')]}
      />,
    );
    expect(screen.getByText('小前')).toBeTruthy();
    expect(container.querySelector('.ap-task')?.textContent).toBe('把首页切出来');
  });
});

// ═══════════════════════════════════════════════════════════════
describe('RosterPanel', () => {
  it('open=false → 面板不带 .open（收起）', () => {
    useKnoweStore.setState({
      convs: { p1: { projectId: 'p1', items: [], members: [member('fe_1', '小前')], banner: null, draft: '' } },
      activeProjectId: 'p1',
    });
    const { container } = render(<RosterPanel open={false} onClose={() => {}} />);
    expect(container.querySelector('.roster-wrap.open')).toBeNull();
  });

  it('open=true → 列出成员，busy 的显示「工作中」', () => {
    useKnoweStore.setState({
      convs: {
        p1: {
          projectId: 'p1', items: [], banner: null,
      draft: '',
          members: [member('fe_1', '小前', '前端', 'busy'), member('be_1', '小后', '后端', 'idle')],
        },
      },
      activeProjectId: 'p1',
    });
    const { container } = render(<RosterPanel open onClose={() => {}} />);

    expect(container.querySelector('.roster-wrap.open')).toBeTruthy();
    expect(container.querySelector('.roster-title')?.textContent).toBe('成员 · 2');
    expect(screen.getByText('工作中')).toBeTruthy();
    expect(screen.getByText('空闲')).toBeTruthy();
    expect(container.querySelector('.r-av.busy')).toBeTruthy();
    expect(container.querySelector('.status-dot.busy')).toBeTruthy();
  });

  it('点收起 → 回调 onClose', () => {
    let closed = false;
    render(<RosterPanel open onClose={() => { closed = true; }} />);
    fireEvent.click(screen.getByLabelText('收起成员面板'));
    expect(closed).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('ToastHost', () => {
  it('store.addNotice → 屏幕上弹出提示（不是 Console）', () => {
    render(<ToastHost />);
    act(() => { useKnoweStore.getState().addNotice('消息未收到服务端回声，发送存疑'); });
    expect(screen.getByText('消息未收到服务端回声，发送存疑')).toBeTruthy();
  });

  it('2.4 秒后自己退场', () => {
    vi.useFakeTimers();
    render(<ToastHost />);
    act(() => { useKnoweStore.getState().addNotice('服务端已重启，正在重新同步'); });
    expect(screen.getByText('服务端已重启，正在重新同步')).toBeTruthy();

    act(() => { vi.advanceTimersByTime(2400 + 400); });
    expect(screen.queryByText('服务端已重启，正在重新同步')).toBeNull();
  });
});
