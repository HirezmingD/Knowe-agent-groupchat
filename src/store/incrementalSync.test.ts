// @vitest-environment jsdom
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fetchIncremental, warmUpIncremental } from './incrementalSync';
import { saveSkeleton, loadSkeleton } from './skeletonCache';
import { useKnoweStore } from './store';

describe('incrementalSync · HTTP 增量预热', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    // 重置 store（zustand 单例跨测试保留 convs，必须清）
    useKnoweStore.setState({ convs: {}, _socket: null });
  });

  it('fetchIncremental 解析 events + last_seq', async () => {
    const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      project_id: 'proj-a', after_seq: 3, last_seq: 5,
      events: [{ type: 'message', seq: 4 }, { type: 'message', seq: 5 }],
    }), { status: 200 }));
    vi.stubGlobal('fetch', mock);

    const r = await fetchIncremental('proj-a', 3);
    expect(r).not.toBeNull();
    expect(r!.events).toHaveLength(2);
    expect(r!.lastSeq).toBe(5);
    // 请求带 after_seq 参数
    const url = String(mock.mock.calls[0]![0]);
    expect(url).toContain('after_seq=3');
  });

  it('非 200 → null（静默降级，快照兜底）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 500 })));
    expect(await fetchIncremental('proj-a', 0)).toBeNull();
  });

  it('网络异常 → null 不抛', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await fetchIncremental('proj-a', 0)).toBeNull();
  });

  it('warmUpIncremental：注入 store + readSeq 前进 + socket 水位抬升', async () => {
    // 预置骨架（readSeq=3）
    saveSkeleton('proj-a', { readSeq: 3 });
    // stub 增量响应
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      project_id: 'proj-a', after_seq: 3, last_seq: 5,
      events: [
        { type: 'message', project_id: 'proj-a', seq: 4, agent_id: 'w1', content: 'four' },
        { type: 'message', project_id: 'proj-a', seq: 5, agent_id: 'w1', content: 'five' },
      ],
    }), { status: 200 })));

    // socket 桩：记录 noteIncremental
    const notes: Array<[string, number]> = [];
    const fakeSocket = { noteIncremental: (pid: string, seq: number) => { notes.push([pid, seq]); } };
    useKnoweStore.setState({ _socket: fakeSocket as never });

    const injected = await warmUpIncremental();
    expect(injected).toBe(1);

    // 1. store 已注入 2 条消息（纯数据，convs 存在）
    const conv = useKnoweStore.getState().convs['proj-a'];
    expect(conv).toBeDefined();
    expect(conv!.items.filter((i) => i.kind === 'agent')).toHaveLength(2);

    // 2. socket 水位抬升到 5
    expect(notes).toEqual([['proj-a', 5]]);

    // 3. 骨架 readSeq 前进到 5
    expect(loadSkeleton('proj-a')!.readSeq).toBe(5);
  });

  it('无骨架 → 预热返回 0（不做任何事）', async () => {
    expect(await warmUpIncremental()).toBe(0);
  });

  it('空增量（events: []）→ 不注入、readSeq 不动', async () => {
    saveSkeleton('proj-a', { readSeq: 9 });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      project_id: 'proj-a', after_seq: 9, last_seq: 9, events: [],
    }), { status: 200 })));
    const injected = await warmUpIncremental();
    expect(injected).toBe(0);
    expect(loadSkeleton('proj-a')!.readSeq).toBe(9);
    expect(useKnoweStore.getState().convs['proj-a']).toBeUndefined();
  });
});
