/**
 * forward.v10231.test.ts — [v1.0.23.1] 转发功能前端测试
 *
 * 覆盖：
 * * forwardMessages 协议：content=配言、forwarded 结构化载荷逐字段；
 * * 空附言：content=''、comment=''（模板尾由后端补空）；
 * * user_echo 重放：forwarded 恢复 + 主文案=配言（修复 B4，模板串永不上屏）。
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { applyEvent, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES, type Conv } from './state';
import { useKnoweStore } from './store';
import type { InboundEvent } from '../contract/envelope';
import { resetStore, seedConv, activate, installSocketSpy, type SocketSpy } from '../components/__testkit';
import type { ForwardItem } from './state';

// 可变 Conv（applyEvent 会写 c.items；store 里的 conv 被 immer 冻结，不能用于 apply）
function conv(projectId = 'p1'): Conv {
  return { projectId, projectName: projectId, items: [], members: [], banner: null, draft: '', unread: 0 };
}

function apply(c: ReturnType<typeof conv>, ev: unknown): void {
  applyEvent(c, ev as InboundEvent, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
}

const base = { project_id: 'p1', ts: '2026-08-03T00:00:00Z' };

const fwdItem = (): ForwardItem => ({
  text: '你好，需要帮忙吗？',
  markdown: true,
  sourceName: '陆可 · 前端',
  sourceProjectName: '电商项目',
  sourceRef: { projectId: 'p_src', itemKey: 'k1' },
});

describe('forwardMessages · 出站协议（PRD R3：模板交后端，前端只发结构化载荷）', () => {
  let spy: SocketSpy;

  beforeEach(() => {
    resetStore();
    seedConv('p1', { name: '目标群' });
    activate('p1');
    spy = installSocketSpy();
  });

  it('content = 用户配言；forwarded 携带来源/原文/配言', () => {
    useKnoweStore.getState().forwardMessages(['p1'], [fwdItem()], '帮我看看这个');

    expect(spy.sent).toHaveLength(1);
    const s = spy.sent[0]!;
    expect(s.content).toBe('帮我看看这个');
    const f = s.forwarded as Record<string, unknown>;
    expect(f).toMatchObject({
      sourceName: '陆可 · 前端',
      sourceProjectName: '电商项目',
      originalText: '你好，需要帮忙吗？',
      comment: '帮我看看这个',
      markdown: true,
    });
    expect(f.sourceRef).toEqual({ projectId: 'p_src', itemKey: 'k1' });
  });

  it('空附言 → content=""、comment=""（模板尾留空交 LLM 判断）', () => {
    useKnoweStore.getState().forwardMessages(['p1'], [fwdItem()], '');

    expect(spy.sent).toHaveLength(1);
    const s = spy.sent[0]!;
    expect(s.content).toBe('');
    expect((s.forwarded as Record<string, unknown>).comment).toBe('');
  });

  it('乐观渲染：气泡主文案 = 配言（displayText），不显示模板串', () => {
    useKnoweStore.getState().forwardMessages(['p1'], [fwdItem()], '帮我看看这个');

    const item = useKnoweStore.getState().convs.p1?.items[0];
    expect(item).toBeDefined();
    expect(item?.kind).toBe('user');
    const u = item as { text: string; forwarded?: { comment?: string } };
    expect(u.text).toBe('帮我看看这个');   // 模板串永不上屏
    expect(u.forwarded?.comment).toBe('帮我看看这个');
  });

  it('多目标：同一附言发所有目标（D5 现状语义）', () => {
    seedConv('p2', { name: '第二个群' });
    useKnoweStore.getState().forwardMessages(['p1', 'p2'], [fwdItem()], '群发附言');

    expect(spy.sent).toHaveLength(2);
    expect(spy.sent[0]!.content).toBe('群发附言');
    expect(spy.sent[1]!.content).toBe('群发附言');
  });
});

describe('user_echo 重放 · 转发标记恢复（修复 B4）', () => {
  it('未命中乐观气泡（重进会话）→ 新建气泡：主文案=配言、forwarded 恢复、模板串不上屏', () => {
    const c = conv('p1');
    apply(c, {
      type: 'user_echo',
      content: '转发时配的言',
      client_msg_id: 'cm_x',
      seq: 7,
      forwarded: {
        sourceName: '陆可 · 前端',
        sourceProjectName: '电商项目',
        originalText: '原始消息原文',
        comment: '转发时配的言',
        markdown: true,
      },
      ...base,
    });

    expect(c.items).toHaveLength(1);
    const u = c.items[0] as { kind: string; text: string; forwarded?: Record<string, unknown> };
    expect(u.kind).toBe('user');
    expect(u.text).toBe('转发时配的言');
    expect(u.forwarded).toBeDefined();
    expect(u.forwarded?.sourceName).toBe('陆可 · 前端');
    expect(u.forwarded?.sourceProjectName).toBe('电商项目');
    expect(u.forwarded?.originalText).toBe('原始消息原文');
  });

  it('旧数据（无 forwarded）→ 按现状显示 content，不更劣化', () => {
    const c = conv('p1');
    apply(c, { type: 'user_echo', content: '用户引用了 陆可 的 "旧文"，用户说：""', client_msg_id: 'cm_old', seq: 1, ...base });

    const u = c.items[0] as { text: string; forwarded?: unknown };
    expect(u.text).toBe('用户引用了 陆可 的 "旧文"，用户说：""');
    expect(u.forwarded).toBeUndefined();
  });
});

describe('registerProject · 转发目标自愈（现状保持）', () => {
  it('私聊目标缺失 → 按成员名补建会话，不抛错', () => {
    seedConv('p1', { name: '群A', members: [] });
    expect(() => {
      useKnoweStore.getState().forwardMessages(['dm:p1:ux_1'], [fwdItem()], '私聊转发');
    }).not.toThrow();
    expect(useKnoweStore.getState().convs['dm:p1:ux_1']).toBeDefined();
  });
});
