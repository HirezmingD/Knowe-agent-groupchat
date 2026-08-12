/**
 * batchB.test.tsx — 批次 B：聊天区
 *
 * 重点回归的是 v0.2 踩过的坑：
 *   · 乐观三态（pending → confirmed → suspect）在屏幕上必须区分得出
 *   · 流式聚合 → message 收尾定格（三点输入反馈必须消失）
 *   · 空 content 且无流 → 不渲染空气泡
 *   · Composer 永不因 conn 卸载（断线时输入框还在）
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useKnoweStore } from '../store/store';
import type { Item, ConnStatus, ProducedFile } from '../store/state';
import { resetStore, seedConv, activate, member, installSocketSpy } from './__testkit';

import ChatStream from './ChatStream';
import MessageBubble from './MessageBubble';
import StreamBubble from './StreamBubble';
import SystemLine from './SystemLine';
import EmptyState from './EmptyState';
import Composer from './Composer';
import { FileCardList } from './FileCard';

const noop = (): void => {};
// [v1.0.23.5] ChatStream 增加必填 projectId prop：测试固定渲染「会话 p1」。
const stream = (): React.ReactElement => (
  <ChatStream projectId="p1" rosterOpen={false} onToggleRoster={noop} />
);

beforeEach(() => { cleanup(); resetStore(); });

// ═══════════════════════════════════════════════════════════════
describe('MessageBubble · 用户消息三态', () => {
  it('pending → 有发送中标记', () => {
    const { container } = render(
      <MessageBubble kind="user" text="你好" delivery="pending" />,
    );
    expect(container.querySelector('.m-sending')).toBeTruthy();
    expect(container.querySelector('.m-fail')).toBeNull();
    expect(container.querySelector('.bubble.me')).toBeTruthy();
  });

  it('confirmed → 两个标记都没有（安静地送达）', () => {
    const { container } = render(
      <MessageBubble kind="user" text="你好" delivery="confirmed" />,
    );
    expect(container.querySelector('.m-sending')).toBeNull();
    expect(container.querySelector('.m-fail')).toBeNull();
  });

  it('suspect → 出现失败标记（屏幕上看得见，不只在 Console）', () => {
    const { container } = render(
      <MessageBubble kind="user" text="你好" delivery="suspect" />,
    );
    expect(container.querySelector('.m-fail')).toBeTruthy();
    expect(screen.getByLabelText('发送存疑')).toBeTruthy();
  });

  it('agent 首条带名字与头像；分组后不重复', () => {
    const face = { name: '小前', role: '前端', glyph: '小', pal: 'av-a' };
    const { container: first } = render(
      <MessageBubble kind="agent" text="收到" face={face} grouped={false} />,
    );
    expect(first.querySelector('.sender-line')?.textContent).toBe('小前 · 前端');
    expect(first.querySelector('.m-av .avatar')).toBeTruthy();

    cleanup();
    const { container: cont } = render(
      <MessageBubble kind="agent" text="继续" face={face} grouped />,
    );
    expect(cont.querySelector('.mgroup.same')).toBeTruthy();
    expect(cont.querySelector('.sender-line')).toBeNull();
    expect(cont.querySelector('.m-av .avatar')).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════
describe('StreamBubble · 流式', () => {
  it('流式中 → 有三点输入反馈，但不逐字渲染增量正文', () => {
    const { container } = render(<StreamBubble text="正在思" />);
    expect(container.querySelectorAll('.bubble.agent .typing-dots i')).toHaveLength(3);
    expect(screen.getByText('正在输入…')).toBeTruthy();
    expect(screen.queryByText('正在思')).toBeNull();
  });

  it('text 为空也渲染——第一个 delta 未到时的样子', () => {
    const { container } = render(<StreamBubble text="" />);
    expect(container.querySelectorAll('.typing-dots i')).toHaveLength(3);
  });

  it('[v1.0.23.3] 推理面板：流式中实时展示推理段落，默认展开可折叠', () => {
    const { container } = render(
      <StreamBubble reasoning={'先分析问题\n再看数据'} />,
    );
    expect(screen.getByText('AI 推理记录')).toBeTruthy();
    expect(screen.getByText('先分析问题')).toBeTruthy();
    expect(screen.getByText('再看数据')).toBeTruthy();
    const trigger = container.querySelector('.reasoning-trigger') as HTMLButtonElement | null;
    expect(trigger).toBeTruthy();
    expect(trigger?.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('.reasoning-scroll p')).toBeTruthy();
  });

  it('终态先到、首帧未确认时只画通用输入反馈，不抢先露出推理', () => {
    render(
      <StreamBubble
        settling
        reasoning="不该露出的推理"
      />,
    );
    expect(screen.getByText('正在输入')).toBeTruthy();
    expect(screen.queryByText('AI 推理记录')).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════
describe('FileCardList · 有界折叠', () => {
  const files = (count: number): ProducedFile[] => Array.from({ length: count }, (_, index) => ({
    path: `output/file-${index + 1}.txt`,
    name: `file-${index + 1}.txt`,
    ext: 'txt',
  }));

  it.each([0, 6] as const)('%i 个文件无需折叠', (count) => {
    const { container } = render(<FileCardList files={files(count)} projectId="p1" />);
    expect(container.querySelectorAll('.file-card')).toHaveLength(count);
    expect(container.querySelector('.file-card-fold-toggle')).toBeNull();
  });

  it('7 个文件默认只挂载 6 个，并报告隐藏数量', () => {
    const { container } = render(<FileCardList files={files(7)} projectId="p1" />);
    expect(container.querySelectorAll('.file-card')).toHaveLength(6);
    expect(screen.getByText('共 7 个文件，已隐藏 1 个')).toBeTruthy();
    expect(screen.getByRole('button', { name: '展开其余 1 个' }).getAttribute('aria-expanded'))
      .toBe('false');
  });

  it.each([20, 200] as const)('%i 个文件可完整展开并再次收起', async (count) => {
    const user = userEvent.setup();
    const { container } = render(<FileCardList files={files(count)} projectId="p1" />);
    expect(container.querySelectorAll('.file-card')).toHaveLength(6);
    await user.click(screen.getByRole('button', { name: `展开其余 ${count - 6} 个` }));
    expect(container.querySelectorAll('.file-card')).toHaveLength(count);
    expect(screen.getByRole('button', { name: '收起' }).getAttribute('aria-expanded')).toBe('true');
    await user.click(screen.getByRole('button', { name: '收起' }));
    expect(container.querySelectorAll('.file-card')).toHaveLength(6);
  });

  it('按 path 去重后再计算折叠边界，并保持首次出现顺序', () => {
    const duplicate = [...files(7), { ...files(1)[0]!, name: '重复名称.txt' }];
    const { container } = render(<FileCardList files={duplicate} projectId="p1" />);
    expect(container.querySelectorAll('.file-card')).toHaveLength(6);
    expect(screen.getByText('共 7 个文件，已隐藏 1 个')).toBeTruthy();
    expect(container.querySelector('.file-card')?.getAttribute('data-fc-name')).toBe('file-1.txt');
  });

  it('原生按钮支持键盘切换', async () => {
    const user = userEvent.setup();
    const { container } = render(<FileCardList files={files(7)} projectId="p1" />);
    const toggle = screen.getByRole('button', { name: '展开其余 1 个' });
    toggle.focus();
    await user.keyboard('{Enter}');
    expect(container.querySelectorAll('.file-card')).toHaveLength(7);
  });

  it('不同消息的展开状态相互隔离', async () => {
    const user = userEvent.setup();
    const { getByTestId } = render(
      <>
        <div data-testid="first"><FileCardList files={files(7)} projectId="p1" /></div>
        <div data-testid="second"><FileCardList files={files(7)} projectId="p1" /></div>
      </>,
    );
    await user.click(within(getByTestId('first')).getByRole('button', { name: '展开其余 1 个' }));
    expect(getByTestId('first').querySelectorAll('.file-card')).toHaveLength(7);
    expect(getByTestId('second').querySelectorAll('.file-card')).toHaveLength(6);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('SystemLine', () => {
  it('info → .sysline', () => {
    const { container } = render(<SystemLine text="小前 已加入项目" />);
    expect(container.querySelector('.sysline')).toBeTruthy();
    expect(container.querySelector('.sysline.err')).toBeNull();
  });

  it('error → .sysline.err（错的要看得出是错的）', () => {
    const { container } = render(<SystemLine text="引擎异常" level="error" />);
    expect(container.querySelector('.sysline.err')).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════
describe('EmptyState', () => {
  it('未选项目 → 空态引导文案', () => {
    const { container } = render(<EmptyState />);
    expect(container.querySelector('.empty')).toBeTruthy();
    expect(screen.getByText('选择一个项目开始')).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════
describe('ChatStream · 消息流分发', () => {
  it('items 逐条分发到正确组件', () => {
    const items: Item[] = [
      { kind: 'user', text: '做个官网', cmid: 'c1', delivery: 'confirmed' },
      { kind: 'agent', agentId: 'fe_1', text: '收到', streaming: false },
      { kind: 'system', text: '小前 已加入项目', level: 'info' },
    ];
    seedConv('p1', { name: '官网改版', items, members: [member('fe_1', '小前')] });
    activate('p1');

    const { container } = render(stream());
    expect(container.querySelector('.bubble.me')?.textContent).toBe('做个官网');
    expect(container.querySelector('.bubble.agent')?.textContent).toBe('收到');
    expect(container.querySelector('.sysline')?.textContent).toBe('小前 已加入项目');
  });

  it('★ 空 content 且无流 → 不渲染空气泡', () => {
    const items: Item[] = [{ kind: 'agent', agentId: 'fe_1', text: '', streaming: false }];
    seedConv('p1', { items, members: [member('fe_1', '小前')] });
    activate('p1');

    const { container } = render(stream());
    expect(container.querySelector('.bubble.agent')).toBeNull();
  });

  it('★ 流式中有三点反馈；收尾后三点消失（定格）', () => {
    seedConv('p1', {
      items: [{ kind: 'agent', agentId: 'fe_1', text: '正在', streaming: true }],
      members: [member('fe_1', '小前')],
    });
    activate('p1');

    const { container, rerender } = render(stream());
    expect(container.querySelectorAll('.typing-dots i')).toHaveLength(3);

    // message 收尾（applyEvent 会把 streaming 置 false）
    act(() => {
      useKnoweStore.setState((s) => ({
        convs: {
          ...s.convs,
          p1: {
            ...s.convs['p1']!,
            items: [{ kind: 'agent', agentId: 'fe_1', text: '正在写', streaming: false }],
          },
        },
      }));
    });
    rerender(stream());

    expect(container.querySelector('.typing-dots')).toBeNull();
    expect(container.querySelector('.bubble.agent')?.textContent).toBe('正在写');
  });

  it('横幅（崩溃恢复）显示，可关闭', () => {
    seedConv('p1', { banner: '已从中断中恢复' });
    activate('p1');

    render(stream());
    expect(screen.getByText('已从中断中恢复')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('关闭提示'));
    expect(screen.queryByText('已从中断中恢复')).toBeNull();
  });

  it('头部显示项目名与成员数', () => {
    seedConv('p1', { name: '官网改版', members: [member('fe_1', '小前'), member('be_1', '小后', '后端')] });
    activate('p1');

    const { container } = render(stream());
    expect(container.querySelector('.ch-title')?.textContent).toBe('官网改版');
    expect(container.querySelector('.ch-status')?.textContent).toContain('2 位成员');
  });
});

// ═══════════════════════════════════════════════════════════════
describe('Composer', () => {
  it('输入 + Enter → store.sendMessage（乐观气泡当场出现）', () => {
    const spy = installSocketSpy();
    seedConv('p1');
    activate('p1');

    render(<Composer />);
    const ta = screen.getByLabelText('消息输入框');
    fireEvent.change(ta, { target: { value: '你好' } });
    fireEvent.keyDown(ta, { key: 'Enter' });

    expect(spy.sent).toEqual([{ content: '你好', projectId: 'p1' }]);

    const items = useKnoweStore.getState().convs['p1']?.items ?? [];
    expect(items.length).toBe(1);
    expect(items[0]).toMatchObject({ kind: 'user', text: '你好', delivery: 'pending' });
  });

  it('Shift+Enter → 换行，不发送', () => {
    const spy = installSocketSpy();
    seedConv('p1'); activate('p1');

    render(<Composer />);
    const ta = screen.getByLabelText('消息输入框');
    fireEvent.change(ta, { target: { value: '第一行' } });
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true });

    expect(spy.sent.length).toBe(0);
  });

  it('空白内容 → 发送键 idle 且点不动', () => {
    const spy = installSocketSpy();
    seedConv('p1'); activate('p1');

    const { container } = render(<Composer />);
    expect(container.querySelector('.send.idle')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('发送'));
    expect(spy.sent.length).toBe(0);
  });

  it('★ 断线时输入框仍在 DOM 里，且仍可发送（不吞消息）', () => {
    const spy = installSocketSpy();
    seedConv('p1'); activate('p1');
    act(() => { useKnoweStore.setState({ conn: 'closed' as ConnStatus }); });

    render(<Composer />);
    const ta = screen.getByLabelText('消息输入框');
    expect(ta).toBeTruthy();                       // 输入框没消失（v0.2 事故的专属回归）

    fireEvent.change(ta, { target: { value: '断线也要发' } });
    fireEvent.keyDown(ta, { key: 'Enter' });
    expect(spy.sent.length).toBe(1);               // 交给 transport 响亮失败 + 回声哨兵
  });
});
