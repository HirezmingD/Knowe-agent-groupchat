/**
 * [v1.0.23.6] 启动预热：HTTP 增量 + 骨架 readSeq 水位对齐。
 *
 * 链路（架构设计 §3.4）：
 *   读全部骨架（同步）→ 对每个项目发 GET /api/events?after_seq=readSeq
 *   → 事件按 seq 升序注入 store（applyIncrementalEvents，纯数据）
 *   → socket.noteIncremental 抬升水位（防止 WS replay 重复投递）
 *   → 骨架 readSeq 前进到 last_seq
 *
 * 原则：
 * · 增量只是「预热加速」——WS 全量快照仍是最终基准，到达后整体重建覆盖；
 * · 增量失败静默降级（骨架丢了/接口挂了都只是慢，不是错）；
 * · 与 WS 实时事件重复：seq 幂等（水位对齐）天然挡掉。
 */

import { runtimeHttpBase } from '../shared/runtimeEndpoints';
import { runtimeFetch } from '../shared/runtimeFetch';
import { useKnoweStore } from './store';
import { loadAllSkeletons, saveSkeleton } from './skeletonCache';
import type { InboundEvent } from '../contract/envelope';

/** 单项目增量拉取（无 socket 时也能独立工作；失败返回 null → 调用方静默跳过）。 */
export async function fetchIncremental(
  projectId: string,
  afterSeq: number,
): Promise<{ events: InboundEvent[]; lastSeq: number } | null> {
  try {
    const url = `${runtimeHttpBase()}/api/events?project_id=${encodeURIComponent(projectId)}&after_seq=${afterSeq}`;
    const res = await runtimeFetch(url, { cache: 'no-store' });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      events?: unknown[];
      last_seq?: unknown;
    };
    const events = Array.isArray(data.events) ? (data.events as InboundEvent[]) : [];
    const lastSeq = typeof data.last_seq === 'number' ? data.last_seq : afterSeq;
    return { events, lastSeq };
  } catch {
    return null; // 网络/解析失败 → 静默降级（快照兜底）
  }
}

/**
 * 启动预热：读取全部骨架 → 对每个项目拉增量 → 注入 store + 抬升水位 + 骨架前进。
 * 返回注入的项目数（测试/诊断用）。并发拉取、逐项目独立失败。
 */
export async function warmUpIncremental(): Promise<number> {
  const skeletons = loadAllSkeletons();
  if (skeletons.size === 0) return 0;

  const projectIds = Array.from(skeletons.keys());
  const results = await Promise.allSettled(
    projectIds.map(async (pid) => {
      const sk = skeletons.get(pid)!;
      const fetched = await fetchIncremental(pid, sk.readSeq);
      if (!fetched || fetched.events.length === 0) return false;

      // 1. 纯数据注入（不 bump/不算未读——快照到达后整体重建覆盖）
      useKnoweStore.getState().applyIncrementalEvents(pid, fetched.events);
      // 2. 抬升 socket 水位：防止 WS replay 把同批旧事件再投一遍
      const socket = useKnoweStore.getState()._socket;
      socket?.noteIncremental(pid, fetched.lastSeq);
      // 3. 骨架 readSeq 前进（下次启动从这里继续）
      saveSkeleton(pid, { readSeq: fetched.lastSeq });
      return true;
    }),
  );

  return results.filter((r) => r.status === 'fulfilled' && r.value === true).length;
}
