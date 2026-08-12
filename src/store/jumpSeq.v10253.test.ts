/**
 * [v1.0.25.3] 「跳转到消息出处」对齐：worker 完成时 completion_view_v1 与 message
 * 各占一个 seq（实测 29185/29186 成对），抽屉 /history 只留 message 事件 →
 * 抽屉 seq = message 的 seq。聊天流里气泡由 view_v1 权威投影创建（message 被
 * 拒收），若气泡 seq 停在 view_v1 的号上，跳转按抽屉 seq 找不到气泡 → 点击无反应。
 *
 * 修复：message 被拒收时也要把气泡 seq 收尾成 message 的 seq（对齐抽屉）。
 * 本测试用真实落盘事件（seq 29185/29186）复刻该时序。
 */
import { describe, it, expect } from 'vitest';
import { applyEvent, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES, type Conv } from './state';
import type { InboundEvent } from '../contract/envelope';

function conv(projectId = 'p1'): Conv {
  return { projectId, projectName: projectId, items: [], members: [], banner: null, draft: '', unread: 0 };
}

function apply(c: Conv, ev: unknown): void {
  applyEvent(c, ev as InboundEvent, DEFAULT_AGENTS, DEFAULT_ROLE_TYPES);
}

// 真实落盘事件（project_20260809114029.jsonl seq 29185/29186），字段精简到 reducer 读的
const viewV1 = {
  type: 'completion_view_v1',
  completion_id: 'cmp_e75693145e34114d44f82038',
  task_id: 'task_c67c003b101f880a53116644',
  attempt_id: 'attempt_62f396ae22a21381a4a78ea2',
  agent_id: 'mkt_1',
  version: 1,
  status: 'SUCCEEDED',
  terminal: true,
  rendered_text: '报告已完成并保存。Hirze，以下是交付结果：',
  user_visible: { summary: '报告已完成并保存。', artifacts: [], gaps: [], next_actions: [] },
  seq: 29185,
};

const message = {
  type: 'message',
  completion_id: 'cmp_e75693145e34114d44f82038',
  task_id: 'task_c67c003b101f880a53116644',
  attempt_id: 'attempt_62f396ae22a21381a4a78ea2',
  agent_id: 'mkt_1',
  status: 'SUCCEEDED',
  content: '报告已完成并保存。Hirze，以下是交付结果：',
  seq: 29186,
};

describe('worker 完成消息 seq 对齐（跳转到消息出处）', () => {
  it('view_v1 先到建气泡（seq=29185），message 后到被拒收但气泡 seq 收尾成 message 的号（29186）', () => {
    const c = conv();
    apply(c, viewV1);

    const afterView = c.items[c.items.length - 1];
    expect(afterView?.kind).toBe('agent');
    expect((afterView as { seq?: number }).seq).toBe(29185);

    apply(c, message);

    const final = c.items[c.items.length - 1];
    expect(final?.kind).toBe('agent');
    // 气泡还在（message 被权威投影拒收，不另起气泡）
    expect(c.items.filter((it) => it.kind === 'agent')).toHaveLength(1);
    // 关键断言：seq 对齐到抽屉 /history 的号（message seq），跳转才能命中
    expect((final as { seq?: number }).seq).toBe(29186);
  });

  it('纯 message 消息（PM/用户，无 view_v1）：气泡 seq 就是 message 的号，天然对齐', () => {
    const c = conv();
    apply(c, { type: 'message', agent_id: 'coordinator', content: '你好', seq: 530 });
    const item = c.items[c.items.length - 1];
    expect((item as { seq?: number }).seq).toBe(530);
  });
});
