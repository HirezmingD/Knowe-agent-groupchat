// @vitest-environment jsdom
/**
 * [v1.0.39-B3] 消息层缓存单测：读写 roundtrip / 损坏降级 / schema 守卫 / 防抖 /
 * 构建过滤（streaming/审批卡/空气泡）/ 截断上限 / 缓存→Item 转换。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  loadMessageCache,
  saveMessageCache,
  scheduleMessageSave,
  flushMessageCache,
  clearMessageCache,
  emptyMessageCache,
  buildMessageCacheFromConvs,
  cachedToItem,
  CACHE_MSG_LIMIT,
  type MessageCache,
} from './messageCache';
import type { Conv, Item } from './state';

describe('messageCache', () => {
  const KEY = 'knowe.sessionMsg.v1';

  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function sampleCache(): MessageCache {
    return {
      schemaVersion: 1,
      savedAt: 123,
      byProject: {
        p1: [
          { kind: 'user', text: '早上好', seq: 1, ts: 1000, cmid: 'c1' },
          { kind: 'agent', text: '收到！', seq: 2, ts: 2000, agentId: 'fe_1', reasoning: '思考中' },
          { kind: 'system', text: 'XX 已加入', level: 'info' },
        ],
      },
    };
  }

  it('保存→读取 roundtrip 完整', () => {
    saveMessageCache(sampleCache());
    const loaded = loadMessageCache();
    expect(loaded).not.toBeNull();
    expect(loaded!.byProject.p1).toHaveLength(3);
    expect(loaded!.byProject.p1![0]!.text).toBe('早上好');
    expect(loaded!.byProject.p1![1]!.agentId).toBe('fe_1');
    expect(loaded!.byProject.p1![2]!.level).toBe('info');
    expect(loaded!.savedAt).toBeGreaterThan(0);
  });

  it('损坏 JSON → null（走现状流程）', () => {
    localStorage.setItem(KEY, '{oops');
    expect(loadMessageCache()).toBeNull();
  });

  it('schema 版本不符 → null', () => {
    localStorage.setItem(KEY, JSON.stringify({ schemaVersion: 99, byProject: {} }));
    expect(loadMessageCache()).toBeNull();
  });

  it('空缓存结构默认', () => {
    const e = emptyMessageCache();
    expect(e.schemaVersion).toBe(1);
    expect(e.byProject).toEqual({});
  });

  it('防抖：多次 schedule 只落一次盘，build 回调延迟执行', () => {
    const build = vi.fn(() => sampleCache());
    scheduleMessageSave(build);
    scheduleMessageSave(build);
    scheduleMessageSave(build);
    expect(localStorage.getItem(KEY)).toBeNull();   // 未到 1200ms
    expect(build).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1300);
    expect(build).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(KEY)).not.toBeNull();
  });

  it('flush 立即写且清定时器', () => {
    const build = vi.fn(() => sampleCache());
    scheduleMessageSave(build);
    flushMessageCache(build);
    expect(localStorage.getItem(KEY)).not.toBeNull();
    expect(build).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1300);
    expect(build).toHaveBeenCalledTimes(1);          // 定时器已清，不再二次写
  });

  it('clear 清空', () => {
    saveMessageCache(sampleCache());
    clearMessageCache();
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(loadMessageCache()).toBeNull();
  });

  function convWith(items: Item[]): Conv {
    return {
      projectId: 'p1', projectName: '测试', items, members: [], banner: null, draft: '',
    } as unknown as Conv;
  }

  it('构建：过滤 streaming / 审批卡 / 空气泡，取最近条数正序', () => {
    const items: Item[] = [
      { kind: 'user', text: '第一条', seq: 1, cmid: 'c1', delivery: 'confirmed' },
      { kind: 'agent', text: '还在流', seq: 2, agentId: 'a1', streaming: true },
      { kind: 'approval', cardId: 'k1', projectId: 'p1', tool: 't', card: {} as never, state: 'pending', expiresAt: 'x' },
      { kind: 'agent', text: '', seq: 3, agentId: 'a1' },        // 空气泡
      { kind: 'agent', text: '落定回复', seq: 4, agentId: 'a1', reasoning: '想', reasoningSeconds: 1.2 },
    ];
    const cache = buildMessageCacheFromConvs({ p1: convWith(items) });
    const msgs = cache.byProject.p1;
    expect(msgs).toHaveLength(2);                     // 只留 第一条 + 落定回复
    expect(msgs![0]!.text).toBe('第一条');
    expect(msgs![1]!.text).toBe('落定回复');
    expect(msgs![1]!.reasoning).toBe('想');
    expect(msgs![1]!.reasoningSeconds).toBe(1.2);
  });

  it('构建：超过上限截断为最近 CACHE_MSG_LIMIT 条', () => {
    const items: Item[] = [];
    for (let i = 1; i <= CACHE_MSG_LIMIT + 20; i++) {
      items.push({ kind: 'user', text: `msg-${i}`, seq: i, cmid: `c${i}`, delivery: 'confirmed' });
    }
    const cache = buildMessageCacheFromConvs({ p1: convWith(items) });
    const msgs = cache.byProject.p1;
    expect(msgs).toHaveLength(CACHE_MSG_LIMIT);
    expect(msgs![0]!.text).toBe(`msg-${CACHE_MSG_LIMIT + 20 - CACHE_MSG_LIMIT + 1}`);  // 第 21 条起
    expect(msgs![CACHE_MSG_LIMIT - 1]!.text).toBe(`msg-${CACHE_MSG_LIMIT + 20}`);       // 最后一条
  });

  it('cachedToItem：user → confirmed 已确认', () => {
    const it = cachedToItem({ kind: 'user', text: 'hi', seq: 5, ts: 100, cmid: 'c9' });
    expect(it).not.toBeNull();
    expect(it!.kind).toBe('user');
    if (it!.kind === 'user') {
      expect(it!.delivery).toBe('confirmed');
      expect(it!.cmid).toBe('c9');
      expect(it!.seq).toBe(5);
    }
  });

  it('cachedToItem：agent 带推理字段', () => {
    const it = cachedToItem({ kind: 'agent', text: 'ok', seq: 6, agentId: 'a1', reasoning: 'r', reasoningSeconds: 3 });
    expect(it!.kind).toBe('agent');
    if (it!.kind === 'agent') {
      expect(it!.agentId).toBe('a1');
      expect(it!.reasoning).toBe('r');
      expect(it!.reasoningSeconds).toBe(3);
    }
  });

  it('cachedToItem：system 默认 info', () => {
    const it = cachedToItem({ kind: 'system', text: 's' });
    expect(it!.kind).toBe('system');
    if (it!.kind === 'system') expect(it!.level).toBe('info');
  });

  it('cachedToItem：缺文本 → null', () => {
    expect(cachedToItem({ kind: 'user', text: '' })).toBeNull();
  });
});
