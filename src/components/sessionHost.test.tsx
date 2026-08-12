/**
 * sessionHost.test.tsx — [v1.0.23.5] 会话视图常驻内存 · 宿主行为回归
 *
 * 验证（PRD 验收标准的前三条）：
 *   1. 懒创建：会话第一次激活才挂载实例
 *   2. 常驻：切走不销毁；切回实例仍在（DOM 同一引用）
 *   3. 显隐：active class 跟随活动会话（opacity 切换）
 *   4. 滚动位置保持：切走再切回，scrollTop 原样（微信式切换的核心）
 *   5. 订阅隔离：A 会话数据更新不触碰 B 会话实例的内容
 *
 * 环境：jsdom（文件头声明）。jsdom 无 ResizeObserver/scrollTo 平滑实现 → 打桩。
 */
// @vitest-environment jsdom

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { useKnoweStore } from '../store/store';
import { resetStore, seedConv, activate } from './__testkit';
import type { Item } from '../store/state';
import SessionHost from './SessionHost';

// jsdom 缺失的浏览器 API 打桩
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
(globalThis as unknown as { scrollTo: unknown }).scrollTo = (): void => {};

const noop = (): void => {};
const host = (): React.ReactElement => (
  <SessionHost rosterOpen={false} onToggleRoster={noop} />
);

function msg(text: string): Item {
  return { kind: 'user', text, ts: Date.now(), seq: Math.random() } as Item;
}

beforeEach(() => {
  resetStore();
});

describe('SessionHost · 懒创建与常驻', () => {
  it('首次激活才创建实例；切走不销毁；切回实例仍在', () => {
    seedConv('p1', { name: '群A', items: [msg('A1')] });
    seedConv('p2', { name: '群B', items: [msg('B1')] });

    const { container } = render(host());
    expect(container.querySelectorAll('.session').length).toBe(0); // 未激活 → 无实例

    act(() => activate('p1'));
    expect(container.querySelectorAll('.session').length).toBe(1);

    act(() => activate('p2'));
    expect(container.querySelectorAll('.session').length).toBe(2); // p1 常驻 + p2 懒创建

    act(() => activate('p1'));
    expect(container.querySelectorAll('.session').length).toBe(2); // 常驻不销毁
  });

  it('active class 跟随活动会话（其余 opacity 隐藏）', () => {
    seedConv('p1', { name: '群A', items: [msg('A1')] });
    seedConv('p2', { name: '群B', items: [msg('B1')] });

    const { container } = render(host());
    act(() => activate('p1'));
    expect(container.querySelector('.session.active')?.textContent).toContain('群A');

    act(() => activate('p2'));
    expect(container.querySelector('.session.active')?.textContent).toContain('群B');
    expect(container.querySelectorAll('.session:not(.active)').length).toBe(1);
  });
});

describe('SessionHost · 滚动位置保持（微信式切换）', () => {
  it('切走再切回：实例不销毁，scrollTop 原样', () => {
    seedConv('p1', { name: '群A', items: [msg('A1'), msg('A2'), msg('A3')] });
    seedConv('p2', { name: '群B', items: [msg('B1')] });

    const { container } = render(host());
    act(() => activate('p1'));

    const p1Msgs = container.querySelector('.session.active .msgs') as HTMLElement;
    expect(p1Msgs).toBeTruthy();
    p1Msgs.scrollTop = 320; // 用户滚到中部

    act(() => activate('p2'));
    act(() => activate('p1'));

    const p1MsgsAgain = container.querySelector('.session.active .msgs') as HTMLElement;
    expect(p1MsgsAgain).toBe(p1Msgs); // 同一个 DOM 节点（未重建）
    expect(p1MsgsAgain.scrollTop).toBe(320); // 位置原样
  });

  it('两个会话各持各的滚动位置，互不干扰', () => {
    seedConv('p1', { name: '群A', items: [msg('A1'), msg('A2'), msg('A3')] });
    seedConv('p2', { name: '群B', items: [msg('B1'), msg('B2'), msg('B3')] });

    const { container } = render(host());
    act(() => activate('p1'));
    (container.querySelector('.session.active .msgs') as HTMLElement).scrollTop = 111;

    act(() => activate('p2'));
    (container.querySelector('.session.active .msgs') as HTMLElement).scrollTop = 222;

    act(() => activate('p1'));
    expect((container.querySelector('.session.active .msgs') as HTMLElement).scrollTop).toBe(111);

    act(() => activate('p2'));
    expect((container.querySelector('.session.active .msgs') as HTMLElement).scrollTop).toBe(222);
  });
});

describe('SessionHost · 订阅隔离', () => {
  it('A 会话追加消息：A 实例更新，B 实例内容原样', () => {
    seedConv('p1', { name: '群A', items: [msg('A1')] });
    seedConv('p2', { name: '群B', items: [msg('B1')] });

    const { container } = render(host());
    act(() => activate('p1'));
    act(() => activate('p2')); // 两个实例都活着

    // 给 p1 追加一条消息（store 局部更新，immer 语义：只有 p1 的 conv 引用变）
    act(() => {
      useKnoweStore.setState((s) => ({
        convs: {
          ...s.convs,
          p1: { ...s.convs.p1!, items: [...s.convs.p1!.items, msg('A2')] },
        },
      }));
    });

    const sessions = container.querySelectorAll('.session');
    // p1 实例显示 2 条，p2 实例仍显示 B1 一条
    const p1Text = (sessions[0] as HTMLElement).textContent ?? '';
    const p2Text = (sessions[1] as HTMLElement).textContent ?? '';
    expect(p1Text).toContain('A1');
    expect(p1Text).toContain('A2');
    expect(p2Text).toContain('B1');
    expect(p2Text).not.toContain('A2');
  });
});
