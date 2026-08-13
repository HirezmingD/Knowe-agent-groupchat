/**
 * state.test.ts — applyEvent 纯状态机（v0.3 全量重建）
 *
 * applyEvent 是全仓库唯一的状态突变实现。它错一点，界面就错一片。
 * 这里逐条事件把它钉死。
 */

import { describe, it, expect } from 'vitest';
import {
  acknowledgeTransientFrame, applyEvent, getConv, registerProject, getProjectList,
  DEFAULT_AGENTS, DEFAULT_ROLE_TYPES,
  type Conv, type UserItem, type AgentItem, type ApprovalItem, type SystemItem,
} from './state';
import type { InboundEvent } from '../contract/envelope';

function conv(projectId = 'p1'): Conv {
  return { projectId, projectName: projectId, items: [], members: [], banner: null, draft: '', unread: 0 };
}

function apply(c: Conv, ev: unknown): void {
  applyEvent(c, ev as InboundEvent, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
}

function paintAgent(c: Conv, index = 0): void {
  const item = c.items[index] as AgentItem | undefined;
  const frameId = item?.transientFrame?.id;
  if (!frameId) throw new Error(`agent item ${index} has no transient frame`);
  expect(acknowledgeTransientFrame(c, frameId)).toBe(true);
}

const base = { project_id: 'p1', ts: '2026-07-12T00:00:00Z' };

// ═══════════════════════════════════════════════════════════════
describe('user_echo · 乐观渲染的确认', () => {
  it('回声匹配到 pending 气泡 → 翻 confirmed（不新增气泡）', () => {
    const c = conv();
    c.items.push({ kind: 'user', text: '你好', cmid: 'cm_1', delivery: 'pending' });

    apply(c, { type: 'user_echo', content: '你好', client_msg_id: 'cm_1', seq: 1, ...base });

    expect(c.items).toHaveLength(1);
    expect((c.items[0] as UserItem).delivery).toBe('confirmed');
  });

  it('回声没有对应的 pending（别的客户端发的）→ 新建 confirmed 气泡', () => {
    const c = conv();
    apply(c, { type: 'user_echo', content: '别处发的', client_msg_id: 'cm_x', seq: 1, ...base });

    expect(c.items).toHaveLength(1);
    expect((c.items[0] as UserItem).delivery).toBe('confirmed');
  });

  it('★ 同一条回声重复到达（握手回放）→ 不重复建气泡', () => {
    const c = conv();
    apply(c, { type: 'user_echo', content: '你好', client_msg_id: 'cm_1', seq: 1, ...base });
    apply(c, { type: 'user_echo', content: '你好', client_msg_id: 'cm_1', seq: 1, ...base });

    expect(c.items).toHaveLength(1);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('stream_delta / message · 流式聚合与收尾', () => {
  it('多条 delta 聚合进同一颗气泡', () => {
    const c = conv();
    apply(c, { type: 'stream_delta', agent_id: 'fe_1', content: '你', seq: 1, ...base });
    apply(c, { type: 'stream_delta', agent_id: 'fe_1', content: '好', seq: 2, ...base });

    expect(c.items).toHaveLength(1);
    const it = c.items[0] as AgentItem;
    expect(it.text).toBe('你好');
    expect(it.streaming).toBe(true);
  });

  it('message 收尾 → 已画过过程态时 streaming 立即落 false', () => {
    const c = conv();
    apply(c, { type: 'stream_delta', agent_id: 'fe_1', content: '你', seq: 1, ...base });
    paintAgent(c);
    apply(c, { type: 'message', agent_id: 'fe_1', content: '你好世界', seq: 2, ...base });

    expect(c.items).toHaveLength(1);
    const it = c.items[0] as AgentItem;
    expect(it.text).toBe('你好世界');
    expect(it.streaming).toBe(false);
  });

  it('★ 空 content 且没有进行中的流 → 不建空气泡', () => {
    const c = conv();
    apply(c, { type: 'message', agent_id: 'fe_1', content: '', seq: 1, ...base });
    expect(c.items).toHaveLength(0);
  });

  it('空 content 但有进行中的流 → 收尾定格，文字保留', () => {
    const c = conv();
    apply(c, { type: 'stream_delta', agent_id: 'fe_1', content: '半句', seq: 1, ...base });
    paintAgent(c);
    apply(c, { type: 'message', agent_id: 'fe_1', content: '', seq: 2, ...base });

    const it = c.items[0] as AgentItem;
    expect(it.text).toBe('半句');
    expect(it.streaming).toBe(false);
  });

  it('★ 同批 thinking → message：最终正文立即写入，但过程态至少保留到首帧回执', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'fe_1', seq: 1, ...base });
    const frameId = (c.items[0] as AgentItem).transientFrame?.id;

    apply(c, { type: 'message', agent_id: 'fe_1', content: '短回复', seq: 2, ...base });

    const protectedItem = c.items[0] as AgentItem;
    expect(protectedItem.text).toBe('短回复');
    expect(protectedItem.streaming).toBe(true);
    expect(protectedItem.transientFrame).toMatchObject({
      id: frameId,
      painted: false,
      settlePending: true,
    });

    expect(acknowledgeTransientFrame(c, frameId!)).toBe(true);
    expect(protectedItem.streaming).toBe(false);
    expect(protectedItem.transientFrame).toBeUndefined();
  });

  it('通用帧保护也覆盖 error 的“秒建秒结”路径', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'fe_1', seq: 1, ...base });
    const frameId = (c.items[0] as AgentItem).transientFrame?.id;

    apply(c, {
      type: 'error', agent_id: 'fe_1', message: '执行失败',
      stage: 'verify', stage_state: 'error', seq: 2, ...base,
    });

    const protectedItem = c.items[0] as AgentItem;
    expect(protectedItem.streaming).toBe(true);
    expect(protectedItem.transientFrame?.settlePending).toBe(true);
    expect(protectedItem.stages?.[protectedItem.stages.length - 1]?.state).toBe('error');

    acknowledgeTransientFrame(c, frameId!);
    expect(protectedItem.streaming).toBe(false);
  });

  it('两个 agent 同时说话 → 各自一颗气泡，不串台', () => {
    const c = conv();
    apply(c, { type: 'stream_delta', agent_id: 'fe_1', content: '前端说', seq: 1, ...base });
    apply(c, { type: 'stream_delta', agent_id: 'be_1', content: '后端说', seq: 2, ...base });
    apply(c, { type: 'stream_delta', agent_id: 'fe_1', content: '完了', seq: 3, ...base });

    expect(c.items).toHaveLength(2);
    expect((c.items[0] as AgentItem).text).toBe('前端说完了');
    expect((c.items[1] as AgentItem).text).toBe('后端说');
  });

  it('agent 首次出现 → 自动进花名册', () => {
    const c = conv();
    apply(c, { type: 'message', agent_id: 'fe_1', content: '在', seq: 1, ...base });
    expect(c.members.map((m) => m.id)).toEqual(['fe_1']);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('审批 · 首个解决为准', () => {
  const card = (cardId: string, seq: number): unknown => ({
    type: 'approval_card', agent_id: 'coordinator', tool: 'propose_agents', card_id: cardId,
    card: {
      status: 'pending_approval', expires_at: '2026-07-12T00:05:00Z', approval_id: cardId,
      proposed: [{ id: 'fe_1', role: '前端' }],
    },
    seq, ...base,
  });

  it('approval_card → 卡片入流，被提议的成员先进花名册', () => {
    const c = conv();
    apply(c, card('ap_1', 1));

    expect(c.items).toHaveLength(1);
    const it = c.items[0] as ApprovalItem;
    expect(it.state).toBe('pending');
    expect(it.tool).toBe('team');
    expect(it.expiresAt).toBe('2026-07-12T00:05:00Z');
    expect(c.members.map((m) => m.id)).toEqual(['fe_1']);
  });

  it.each([
    ['approved', 'confirmed'],
    ['rejected', 'rejected'],
    ['timeout', 'timeout'],
    ['cancelled', 'cancelled'],
  ])('resolution=%s → 卡片落 %s', (resolution, expected) => {
    const c = conv();
    apply(c, card('ap_1', 1));
    apply(c, { type: 'approval_resolved', card_id: 'ap_1', resolution, seq: 2, ...base });

    expect((c.items[0] as ApprovalItem).state).toBe(expected);
  });

  it('★ 双解决：timeout 先到 → 后到的 cancelled 被忽略（首个解决为准）', () => {
    const c = conv();
    apply(c, card('ap_1', 1));
    apply(c, { type: 'approval_resolved', card_id: 'ap_1', resolution: 'timeout', seq: 2, ...base });
    apply(c, { type: 'approval_resolved', card_id: 'ap_1', resolution: 'cancelled', seq: 3, ...base });

    expect((c.items[0] as ApprovalItem).state).toBe('timeout');
  });

  it('★ 双解决：cancelled 先到 → 后到的 timeout 被忽略', () => {
    const c = conv();
    apply(c, card('ap_1', 1));
    apply(c, { type: 'approval_resolved', card_id: 'ap_1', resolution: 'cancelled', seq: 2, ...base });
    apply(c, { type: 'approval_resolved', card_id: 'ap_1', resolution: 'timeout', seq: 3, ...base });

    expect((c.items[0] as ApprovalItem).state).toBe('cancelled');
  });

  it('对不存在的卡的 resolution → 安静忽略，不抛异常', () => {
    const c = conv();
    expect(() => {
      apply(c, { type: 'approval_resolved', card_id: '查无此卡', resolution: 'approved', seq: 1, ...base });
    }).not.toThrow();
    expect(c.items).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('团队生命周期', () => {
  it('agents_created → 成员入驻 + 系统行', () => {
    const c = conv();
    apply(c, {
      type: 'agents_created', agent_id: 'coordinator', count: 2,
      members: [{ id: 'fe_1', role: '前端' }, { id: 'be_1', role: '后端' }],
      seq: 1, ...base,
    });

    expect(c.members).toHaveLength(2);
    expect((c.items[0] as SystemItem).kind).toBe('system');
    expect((c.items[0] as SystemItem).text).toContain('已加入项目');
  });

  it('只有匹配 scope 的 agent_active / agent_idle 改变 roster 可用性', () => {
    const c = conv();
    apply(c, {
      type: 'agents_created', agent_id: 'coordinator', count: 1,
      members: [{ id: 'fe_1', role: '前端' }], seq: 1, ...base,
    });

    apply(c, { type: 'instruction_injected', agent_id: 'coordinator', target_id: 'fe_1', seq: 2, ...base });
    expect(c.members[0]!.state).toBe('idle');

    apply(c, {
      type: 'agent_active', agent_id: 'fe_1', scope_id: 'scope-a', channel_id: 'p1',
      reason: 'worker_turn', seq: 3, ...base,
    });
    expect(c.members[0]!.state).toBe('busy');

    apply(c, { type: 'report_submitted', agent_id: 'fe_1', report_hash: 'h', seq: 4, ...base });
    expect(c.members[0]!.state).toBe('busy');

    apply(c, {
      type: 'agent_idle', agent_id: 'fe_1', scope_id: 'scope-a', channel_id: 'p1',
      reason: 'worker_turn', seq: 5, ...base,
    });
    expect(c.members[0]!.state).toBe('idle');
  });

  it('迟到的 scope A idle 不会关闭 scope B，重复 active 不刷新 busySince', () => {
    const c = conv();
    apply(c, {
      type: 'agents_created', agent_id: 'coordinator', count: 1,
      members: [{ id: 'fe_1', role: '前端' }], seq: 1, ...base,
    });
    apply(c, {
      type: 'agent_active', agent_id: 'fe_1', scope_id: 'scope-a', channel_id: 'p1',
      reason: 'worker_turn', seq: 2, ...base,
    });
    const started = c.members[0]!.busySince;
    apply(c, {
      type: 'agent_active', agent_id: 'fe_1', scope_id: 'scope-a', channel_id: 'p1',
      reason: 'duplicate', seq: 3, ts: '2026-07-12T00:01:00Z', project_id: 'p1',
    });
    expect(c.members[0]!.busySince).toBe(started);

    apply(c, {
      type: 'agent_active', agent_id: 'fe_1', scope_id: 'scope-b', channel_id: 'p1',
      reason: 'worker_turn', seq: 4, ...base,
    });
    apply(c, {
      type: 'agent_idle', agent_id: 'fe_1', scope_id: 'scope-a', channel_id: 'p1',
      reason: 'late', seq: 5, ...base,
    });
    expect(c.members[0]!.state).toBe('busy');
    expect((c.members[0]!.activeScopes && Object.keys(c.members[0]!.activeScopes))).toHaveLength(1);

    apply(c, {
      type: 'agent_idle', agent_id: 'fe_1', scope_id: 'scope-b', channel_id: 'p1',
      reason: 'done', seq: 6, ...base,
    });
    expect(c.members[0]!.state).toBe('idle');
  });

  it('recovery_notice → 系统行', () => {
    const c = conv();
    apply(c, { type: 'recovery_notice', message: '已从中断中恢复', seq: 1, ...base });
    expect((c.items[0] as SystemItem).text).toBe('已从中断中恢复');
  });
});

// ═══════════════════════════════════════════════════════════════
describe('error · 两级错误', () => {
  it('引擎级 error（有 project_id）→ 进会话流，level=error', () => {
    const c = conv();
    apply(c, { type: 'error', message: '引擎炸了', seq: 1, ...base });

    const it = c.items[0] as SystemItem;
    expect(it.kind).toBe('system');
    expect(it.level).toBe('error');
  });

  it('★ 服务器级 error（无 project_id）→ 不进会话流（由 store 送进全局通知）', () => {
    const c = conv();
    apply(c, { type: 'error', message: '畸形帧' });
    expect(c.items).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('state_snapshot · 整体重建', () => {
  const snap = (conversation: unknown[], seq = 10, activity?: unknown[]): unknown => ({
    ...base,
    type: 'state_snapshot', project_id: 'p1', last_seq: seq - 1,
    agents: [], conversation, pending_card: null, seq,
    ...(activity !== undefined ? { activity } : {}),
  });

  it('快照 → 清空后按 conversation 逐条重放', () => {
    const c = conv();
    c.items.push({ kind: 'user', text: '会被清掉的旧内容', cmid: 'x', delivery: 'confirmed' });
    c.banner = '旧横幅';

    apply(c, snap([
      { type: 'user_echo', content: '重建的消息', client_msg_id: 'cm_1', seq: 1, ...base },
      { type: 'message', agent_id: 'fe_1', content: '重建的回复', seq: 2, ...base },
    ]));

    expect(c.items).toHaveLength(2);
    expect((c.items[0] as UserItem).text).toBe('重建的消息');
    expect(c.banner).toBeNull();
  });

  it('★ 花名册从 conversation 里的 agents_created 重建（顶层 agents 不可信）', () => {
    const c = conv();
    apply(c, snap([
      {
        type: 'agents_created', agent_id: 'coordinator', count: 1,
        members: [{ id: 'fe_1', role: '前端' }], seq: 1, ...base,
      },
    ]));

    expect(c.members.map((m) => m.id)).toEqual(['fe_1']);
  });

  it('★ 幂等：同一份快照连应用两次，结果一模一样', () => {
    const c = conv();
    const s = snap([{ type: 'message', agent_id: 'fe_1', content: '只该有一条', seq: 1, ...base }]);

    apply(c, s);
    const first = JSON.stringify(c.items);
    apply(c, s);

    expect(JSON.stringify(c.items)).toBe(first);
    expect(c.items).toHaveLength(1);
  });

  it('conversation 里有坏事件 → 跳过它，不整份快照陪葬', () => {
    const c = conv();
    expect(() => {
      apply(c, snap([
        { type: '不存在的类型', seq: 1, ...base },
        { type: 'message', agent_id: 'fe_1', content: '好的那条', seq: 2, ...base },
      ]));
    }).not.toThrow();

    expect(c.items).toHaveLength(1);
  });

  it('历史快照重放不创建 live-only 帧保护，也不闪回正在输入', () => {
    const c = conv();
    apply(c, snap([
      { type: 'agent_thinking', agent_id: 'fe_1', seq: 1, ...base },
      { type: 'message', agent_id: 'fe_1', content: '历史回复', seq: 2, ...base },
    ]));

    const item = c.items[0] as AgentItem;
    expect(item.text).toBe('历史回复');
    expect(item.streaming).toBe(false);
    expect(item.transientFrame).toBeUndefined();
  });

  it('[v1.0.24.4] 快照带 activity（空账本）→ 卡死的忙碌态被校准回 idle', () => {
    const c = conv();
    // 造一个「卡死」成员：agents_created 入驻 + active 置忙，然后 agent_idle 永远不来
    apply(c, {
      type: 'agents_created', agent_id: 'coordinator', count: 1,
      members: [{ id: 'fe_1', role: '前端' }], seq: 1, ...base,
    });
    apply(c, {
      type: 'agent_active', agent_id: 'fe_1', scope_id: 'scope-a',
      channel_id: 'p1', reason: 'worker_turn', seq: 2, ...base,
    });
    expect(c.members[0]!.state).toBe('busy');          // 卡死态就位

    // 后端现场没人干活 → 空账本 → 整体校准回 idle
    apply(c, snap([
      { type: 'message', agent_id: 'fe_1', content: '历史', seq: 3, ...base },
    ], 4, []));

    expect(c.members[0]!.state).toBe('idle');
    expect(c.members[0]!.busySince).toBeUndefined();
    expect(Object.keys(c.members[0]!.activeScopes ?? {})).toHaveLength(0);
  });

  it('[v1.0.24.4] 快照带 activity（有条目）→ 在账本里的成员置忙，busySince 取最早', () => {
    const c = conv();
    apply(c, {
      type: 'agents_created', agent_id: 'coordinator', count: 2,
      members: [{ id: 'fe_1', role: '前端' }, { id: 'be_1', role: '后端' }], seq: 1, ...base,
    });

    apply(c, snap([
      { type: 'message', agent_id: 'fe_1', content: '历史', seq: 2, ...base },
    ], 3, [
      { agent_id: 'fe_1', scope_id: 'scope-a', channel_id: 'p1', started_at: 2000 },
      { agent_id: 'fe_1', scope_id: 'scope-b', channel_id: 'p1', started_at: 1000 },
      // 别的频道的条目 → 不串台（校准只取 channel_id === p1）
      { agent_id: 'be_1', scope_id: 'x', channel_id: 'other', started_at: 500 },
    ]));

    expect(c.members[0]!.state).toBe('busy');          // fe_1 在账本 → 忙
    expect(c.members[0]!.busySince).toBe(1000);        // 最早 started_at
    expect(Object.keys(c.members[0]!.activeScopes ?? {})).toHaveLength(2);
    expect(c.members[1]!.state).toBe('idle');          // be_1 只在别的频道 → 本会话空闲
  });

  it('[v1.0.24.4] 快照不带 activity（旧后端）→ 不校准，本地忙碌态原样保留', () => {
    const c = conv();
    apply(c, {
      type: 'agents_created', agent_id: 'coordinator', count: 1,
      members: [{ id: 'fe_1', role: '前端' }], seq: 1, ...base,
    });
    apply(c, {
      type: 'agent_active', agent_id: 'fe_1', scope_id: 'scope-a',
      channel_id: 'p1', reason: 'worker_turn', seq: 2, ...base,
    });
    expect(c.members[0]!.state).toBe('busy');

    // 旧后端不带 activity 字段 → 快照重建完全退回老行为（本地运行时复位）
    apply(c, snap([
      { type: 'message', agent_id: 'fe_1', content: '历史', seq: 3, ...base },
    ]));

    expect(c.members[0]!.state).toBe('busy');          // 保留，不校准
  });
});

// ═══════════════════════════════════════════════════════════════
describe('瞬时事件与未知事件', () => {
  it('agent_thinking 建立临时气泡；tool_gen 缺气泡时自愈；纯边界事件仍不造气泡', () => {
    const c = conv();
    apply(c, { type: 'agent_thinking', agent_id: 'fe_1', seq: 1, ...base });
    expect(c.items).toHaveLength(1);
    expect((c.items[0] as AgentItem).streaming).toBe(true);

    const selfHealing = conv();
    apply(selfHealing, {
      type: 'tool_gen', agent_id: 'fe_1', tool_name: 'safe_read_file', seq: 1, ...base,
    });
    expect(selfHealing.items).toHaveLength(1);
    expect((selfHealing.items[0] as AgentItem).activities).toEqual([
      { tool: 'safe_read_file', n: 1, pendingDetail: true },
    ]);

    const boundaries = conv();
    apply(boundaries, { type: 'tool_start', agent_id: 'fe_1', seq: 1, ...base });
    apply(boundaries, { type: 'tool_complete', agent_id: 'fe_1', seq: 2, ...base });
    apply(boundaries, { type: 'project_created', project_id: 'p1', seq: 3, ts: base.ts });
    expect(boundaries.items).toHaveLength(0);
  });

  it('未知事件类型 → 忽略，不抛异常（一条坏事件不许打断整条流）', () => {
    const c = conv();
    expect(() => apply(c, { type: 'turn_end', seq: 1, ...base })).not.toThrow();
    expect(c.items).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════════
describe('多会话管理', () => {
  it('getConv 按需建会话；registerProject 记名字；getProjectList 列全部', () => {
    const convs: Record<string, Conv> = {};
    getConv(convs, 'p1');
    registerProject(convs, 'p2', '官网改版');

    expect(Object.keys(convs).sort()).toEqual(['p1', 'p2']);
    expect(getProjectList(convs)).toEqual([
      { project_id: 'p1', name: 'p1' },
      { project_id: 'p2', name: '官网改版' },
    ]);
  });
});
