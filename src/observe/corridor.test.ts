/**
 * corridor.test.ts — 诊断走廊内核
 *
 * 走廊存在的意义只有一个：**让丢弃不再静默。**
 * 所以最要紧的一条测试是「计数器真的会跳」——旧版走廊的计数器永远是 0，
 * 那才是最坏的情况：一块看起来一切正常的假仪表盘。
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  record, getCorridorState, resetCorridor, subscribeCorridor,
  exportCorridorJSON, hasAlerts, CORRIDOR_MAX,
  type CorridorState, type EventVerdict,
} from './corridor';

beforeEach(() => resetCorridor());

describe('走廊 · 记录', () => {
  it('记一条 → 进尾巴，字段齐全', () => {
    record({ type: 'message', projectId: 'p1', seq: 7, verdict: 'applied', summary: '好' });

    const e = getCorridorState().entries[0]!;
    expect(e.type).toBe('message');
    expect(e.projectId).toBe('p1');
    expect(e.seq).toBe(7);
    expect(e.verdict).toBe('applied');
    expect(e.dir).toBe('in');                        // 默认入站
    expect(e.ts).toMatch(/^\d{4}-\d{2}-\d{2}T/);     // ISO 8601
  });

  it('无 seq 事件记成 -1，无项目记成 -（不许出现 undefined）', () => {
    record({ type: 'pong', verdict: 'bypass' });

    const e = getCorridorState().entries[0]!;
    expect(e.seq).toBe(-1);
    expect(e.projectId).toBe('-');
  });

  it(`尾巴只留最近 ${CORRIDOR_MAX} 条（环形，不会把内存吃光）`, () => {
    for (let i = 0; i < CORRIDOR_MAX + 50; i++) {
      record({ type: 'message', seq: i, verdict: 'applied' });
    }
    const { entries } = getCorridorState();

    expect(entries).toHaveLength(CORRIDOR_MAX);
    expect(entries[0]!.seq).toBe(50);                // 最老的 50 条被挤掉了
    expect(entries.at(-1)!.seq).toBe(CORRIDOR_MAX + 49);
  });
});

describe('走廊 · 计数器（★ 旧版永远是 0 的那四个）', () => {
  it.each([
    ['rejected', 'zodRejected'],
    ['dup', 'seqDropped'],
    ['gap', 'seqDropped'],
    ['failed', 'outboundFailed'],
    ['sentinel', 'sentinelAlerts'],
    ['epoch', 'epochResets'],
  ] as [EventVerdict, keyof CorridorState][])('判定 %s → 计数器 %s +1', (verdict, counter) => {
    record({ type: 'x', verdict });
    expect(getCorridorState()[counter]).toBe(1);
  });

  it('dup 和 gap 共用 seqDropped（都是「seq 层面丢了一条」）', () => {
    record({ type: 'x', verdict: 'dup' });
    record({ type: 'x', verdict: 'gap' });
    expect(getCorridorState().seqDropped).toBe(2);
  });

  it('applied / bypass / buffered 不是告警，不计数', () => {
    record({ type: 'x', verdict: 'applied' });
    record({ type: 'x', verdict: 'bypass' });
    record({ type: 'x', verdict: 'buffered' });

    const s = getCorridorState();
    expect(s.zodRejected + s.seqDropped + s.outboundFailed + s.sentinelAlerts).toBe(0);
    expect(s.entries).toHaveLength(3);               // 但仍然留痕
    expect(hasAlerts()).toBe(false);
  });

  it('任何一个告警计数器亮 → hasAlerts() 为真', () => {
    expect(hasAlerts()).toBe(false);
    record({ type: 'x', verdict: 'rejected' });
    expect(hasAlerts()).toBe(true);
  });
});

describe('走廊 · 订阅', () => {
  it('订阅立刻拿到当前快照，之后每记一条推一次', () => {
    const seen: CorridorState[] = [];
    const off = subscribeCorridor((s) => seen.push(s));

    expect(seen).toHaveLength(1);                    // 立刻推一次当前值
    record({ type: 'message', verdict: 'applied' });
    expect(seen).toHaveLength(2);
    expect(seen[1]!.entries).toHaveLength(1);

    off();
    record({ type: 'message', verdict: 'applied' });
    expect(seen).toHaveLength(2);                    // 退订后不再推
  });

  it('★ 每次推的是新引用（否则 React 不会重渲染，界面永远停在第一帧）', () => {
    const seen: CorridorState[] = [];
    subscribeCorridor((s) => seen.push(s));
    record({ type: 'x', verdict: 'applied' });

    expect(seen[0]).not.toBe(seen[1]);
    expect(seen[0]!.entries).not.toBe(seen[1]!.entries);
  });
});

describe('走廊 · 导出与重置', () => {
  it('导出 JSON 含计数器 + 全部尾巴（失败报告直接甩这个文件）', () => {
    record({ type: 'stream_delta', projectId: 'p1', seq: 3, verdict: 'rejected' });

    const dump = JSON.parse(exportCorridorJSON()) as {
      counters: Record<string, number>;
      entries: unknown[];
      exportedAt: string;
    };

    expect(dump.counters.zodRejected).toBe(1);
    expect(dump.entries).toHaveLength(1);
    expect(dump.exportedAt).toBeTruthy();
  });

  it('重置 → 尾巴与计数器一起归零', () => {
    record({ type: 'x', verdict: 'gap' });
    resetCorridor();

    const s = getCorridorState();
    expect(s.entries).toHaveLength(0);
    expect(s.seqDropped).toBe(0);
  });
});
