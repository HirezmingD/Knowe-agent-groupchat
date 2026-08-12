/**
 * parity.test.ts — 契约一致性（防夹具漂移）
 *
 * 一台会漂移的假后端比没有假后端更坏：它会让测试绿着、产品红着。
 * 所以这里做一件事——**把 FakeKnoweServer 吐出的每一条事件，
 * 喂进前端真实的 Zod 契约（envelope.ts）跑一遍**。夹具说的话，前端必须听得懂。
 *
 * 同一套断言也适用于真后端（backend/ 那边有一份 Python 侧的镜像校验），
 * 两边都对着 envelope.ts 这一个真源。
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import {
  InboundEventSchema,
  NO_SEQ_EVENT_TYPES,
  STRUCTURAL_EVENT_TYPES,
  TRANSIENT_EVENT_TYPES,
  validateInbound,
  normalizeApprovalCard,
  type ApprovalCard,
} from './envelope';
import { FakeKnoweServer, NO_SEQ_TYPES, STRUCTURAL_TYPES } from '../fakeserver/FakeKnoweServer';

let server: FakeKnoweServer;

beforeEach(async () => {
  server = new FakeKnoweServer({ ringMax: 5 });
  await server.start();
});
afterEach(async () => { await server.stop(); });

/** 过 Zod。失败时把 issue 打出来——不许出现「测试红了但不知道哪个字段」 */
function mustPass(event: unknown, label: string): void {
  const r = InboundEventSchema.safeParse(event);
  if (!r.success) {
    throw new Error(`${label} 没过 Zod：${JSON.stringify(r.error.issues)}\n事件：${JSON.stringify(event)}`);
  }
}

// ═══════════════════════════════════════════════════════════════
// 一、夹具的每一条出站事件都必须过前端 Zod
// ═══════════════════════════════════════════════════════════════

describe('parity · 夹具事件 vs 前端 Zod', () => {
  it('聊天全链路的每一条事件都过 Zod', () => {
    const events: Record<string, unknown>[] = [];
    const push = (e: Record<string, unknown>): void => { events.push(e); };

    push(server.emit('p1', { type: 'user_echo', content: '做个官网', client_msg_id: 'cm_1' }));
    push(server.emit('p1', {
      type: 'agent_active', agent_id: 'coordinator', reason: 'coordinator_turn',
      scope_id: 'coordinator:turn-1', channel_id: 'p1', run_id: 'turn-1',
    }));
    push(server.emit('p1', {
      type: 'agent_thinking', agent_id: 'coordinator',
      scope_id: 'coordinator:turn-1', channel_id: 'p1', run_id: 'turn-1',
    }));
    push(server.emit('p1', { type: 'stream_delta', agent_id: 'coordinator', content: '好' }));
    push(server.emit('p1', { type: 'message', agent_id: 'coordinator', content: '好的' }));
    push(server.emit('p1', {
      type: 'agents_created', agent_id: 'coordinator', count: 1,
      members: [{ id: 'fe_1', role: '前端' }],
    }));
    push(server.emit('p1', {
      type: 'instruction_injected', agent_id: 'coordinator', target_id: 'fe_1',
    }));
    push(server.emit('p1', {
      type: 'report_submitted', agent_id: 'fe_1', report_hash: 'abc123',
    }));
    push(server.emit('p1', { type: 'error', agent_id: 'coordinator', message: '炸了' }));
    push(server.emit('p1', {
      type: 'recovery_notice', message: '已恢复', details: { stale_approvals_count: 1 },
    }));

    expect(events.length).toBe(10);
    for (const e of events) mustPass(e, String(e.type));
  });

  it('审批卡（组队/派活）都过 Zod，且顶层 card_id === card.approval_id', () => {
    const cardId = server.proposeAgents('p1', [{ id: 'fe_1', role: '前端' }]);
    const card = server.ringOf('p1').find((e) => e.type === 'approval_card')!;

    mustPass(card, 'approval_card');
    expect(card.card_id).toBe(cardId);
    expect((card.card as Record<string, unknown>).approval_id).toBe(cardId);

    // 归一化层认得它（复提路径缺字段时的兜底）
    const norm = normalizeApprovalCard(card as unknown as ApprovalCard);
    expect(norm.card_id).toBe(cardId);
    expect(norm.tool).toBe('propose_agents');

    const task = server.emit('p1', {
      type: 'approval_card', agent_id: 'coordinator', tool: 'propose_next',
      card_id: 'ap_t1',
      card: {
        status: 'pending_approval', expires_at: new Date().toISOString(),
        approval_id: 'ap_t1', target_id: 'fe_1', instruction: '干活',
      },
    });
    mustPass(task, 'approval_card(propose_next)');
  });

  it('四个 resolution 值全过 Zod（expired 是 v1 遗物，后端从不发）', () => {
    for (const resolution of ['approved', 'rejected', 'timeout', 'cancelled']) {
      mustPass(
        server.emit('p1', { type: 'approval_resolved', card_id: 'ap_1', resolution }),
        `approval_resolved(${resolution})`,
      );
    }
    const bogus = { type: 'approval_resolved', card_id: 'ap_1', resolution: 'expired',
      project_id: 'p1', seq: 99, ts: new Date().toISOString() };
    expect(InboundEventSchema.safeParse(bogus).success).toBe(false);
  });

  it('服务器级事件（无 seq 白名单）都过 Zod', () => {
    for (const e of [
      { type: 'project_created', project_id: 'p1', project_name: '官网' },
      { type: 'replay_complete', project_id: 'p1', last_seq: 3 },
      { type: 'replay_complete', last_seq: 0 },                      // 超时分支：无 project_id
      { type: 'resync_required', last_seq: 3, message: 'ring 淘汰' },
      { type: 'pong' },
      { type: 'error', message: '未知指令' },                        // 服务器级 error：无 seq
    ]) {
      mustPass(e, String(e.type));
    }
  });

  it('快照过 Zod，且 seq = last_seq + 1（快照自己消耗一个号）', () => {
    server.say('p1', 'coordinator', '你好', 2);
    const snap = server.snapshot('p1');

    mustPass(snap, 'state_snapshot');
    expect(snap.seq).toBe((snap.last_seq as number) + 1);
  });
});

// ═══════════════════════════════════════════════════════════════
// 二、白名单在两边必须一字不差
// ═══════════════════════════════════════════════════════════════

describe('parity · 白名单一致性', () => {
  it('无 seq 白名单：夹具 === 契约', () => {
    expect([...NO_SEQ_TYPES].sort()).toEqual([...NO_SEQ_EVENT_TYPES].sort());
  });

  it('结构事件白名单：夹具 === 契约', () => {
    expect([...STRUCTURAL_TYPES].sort()).toEqual([...STRUCTURAL_EVENT_TYPES].sort());
  });

  it('瞬时事件与结构事件不重叠（一条事件不能既进时间线又不进）', () => {
    const overlap = [...STRUCTURAL_EVENT_TYPES].filter((t) => TRANSIENT_EVENT_TYPES.has(t));
    expect(overlap).toEqual([]);
  });

  it('★ 无 seq 白名单事件绝不能带 seq（带了前端水位就废了）', () => {
    for (const t of NO_SEQ_TYPES) {
      expect(() => server.emit('p1', { type: t })).toThrow();
    }
  });
});

// ═══════════════════════════════════════════════════════════════
// 三、字段漂移的负向测试——契约必须挡得住
// ═══════════════════════════════════════════════════════════════

describe('parity · 字段漂移必须被拒收', () => {
  const base = { project_id: 'p1', seq: 1, ts: new Date().toISOString() };

  it('stream_delta 用 text 而不是 content → 拒收', () => {
    expect(validateInbound({ type: 'stream_delta', agent_id: 'a', text: '你好', ...base })).toBeNull();
    expect(validateInbound({ type: 'stream_delta', agent_id: 'a', content: '你好', ...base })).not.toBeNull();
  });

  it('tool_gen 用 tool 而不是 tool_name → 拒收', () => {
    expect(validateInbound({ type: 'tool_gen', agent_id: 'a', tool: 'x', ...base })).toBeNull();
    expect(validateInbound({ type: 'tool_gen', agent_id: 'a', tool_name: 'x', ...base })).not.toBeNull();
  });

  it('引擎级事件缺 seq → 拒收', () => {
    expect(validateInbound({
      type: 'message', agent_id: 'a', content: 'hi', project_id: 'p1',
    })).toBeNull();
  });

  it('审批卡缺 approval_id → 拒收（前端拿不到 card_id 就没法审批）', () => {
    expect(validateInbound({
      type: 'approval_card', agent_id: 'c', tool: 'propose_agents', card_id: 'ap_1',
      card: { status: 'pending_approval', expires_at: 'x', proposed: [] },
      ...base,
    })).toBeNull();
  });

  it('未知事件类型 → 拒收（不抛异常，返回 null）', () => {
    expect(validateInbound({ type: 'turn_end', project_id: 'p1', seq: 1 })).toBeNull();
  });

  it('message 的 content 允许空串（中断回合 / provider 报错文案）', () => {
    expect(validateInbound({ type: 'message', agent_id: 'a', content: '', ...base })).not.toBeNull();
  });
});
