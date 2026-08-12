/**
 * [v1.0.23.6] 会话骨架持久化（localStorage）。
 *
 * 骨架 = 界面状态（行高缓存 + 滚动位置 + 已读水位），不是消息数据——
 * 消息权威在后端 JSONL，前端绝不复制全量（架构设计 §3.1）。
 *
 * · 体积：每会话几十个 ik 行高 + 2 个数字 ≈ 1-5 KB，100 会话 < 500 KB；
 * · schemaVersion 守卫：格式不匹配即整体丢弃重建，绝不因旧数据崩溃；
 * · 骨架丢了只是「慢」，不是「错」——全部数据可从后端重建（可重建原则）。
 */

const SCHEMA_VERSION = 1;
const KEY_PREFIX = 'knowe.skeleton.v1.'; // 前缀含 schemaVersion，升级即换 key 天然隔离

export interface SessionSkeleton {
  schemaVersion: number;
  projectId: string;
  /** ik → 实测行高（px）。来自 HeightStore.exportHeights()。 */
  heights: Record<string, number>;
  /** 上次滚动位置（px）。 */
  scrollTop: number;
  /** 已读到的 seq 水位（增量请求起点）。 */
  readSeq: number;
  /** 时间戳（调试/清理用）。 */
  updatedAt: number;
}

export function skeletonKey(projectId: string): string {
  return `${KEY_PREFIX}${projectId}`;
}

export function loadSkeleton(projectId: string): SessionSkeleton | null {
  try {
    const raw = localStorage.getItem(skeletonKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SessionSkeleton>;
    // schemaVersion 守卫：不匹配（含缺字段）→ 丢弃，走全量重建（慢但不错）
    if (parsed?.schemaVersion !== SCHEMA_VERSION) return null;
    if (typeof parsed.projectId !== 'string' || parsed.projectId !== projectId) return null;
    return {
      schemaVersion: SCHEMA_VERSION,
      projectId,
      heights: typeof parsed.heights === 'object' && parsed.heights !== null
        ? parsed.heights as Record<string, number>
        : {},
      scrollTop: typeof parsed.scrollTop === 'number' && Number.isFinite(parsed.scrollTop)
        ? parsed.scrollTop
        : 0,
      readSeq: typeof parsed.readSeq === 'number' && Number.isFinite(parsed.readSeq)
        ? Math.max(0, parsed.readSeq)
        : 0,
      updatedAt: typeof parsed.updatedAt === 'number' ? parsed.updatedAt : 0,
    };
  } catch {
    // localStorage 异常（隐私模式/禁用）→ 不缓存，降级为全量重建
    return null;
  }
}

export function saveSkeleton(
  projectId: string,
  patch: Partial<Pick<SessionSkeleton, 'heights' | 'scrollTop' | 'readSeq'>>,
): void {
  try {
    const prev = loadSkeleton(projectId);
    const next: SessionSkeleton = {
      schemaVersion: SCHEMA_VERSION,
      projectId,
      heights: patch.heights ?? prev?.heights ?? {},
      scrollTop: patch.scrollTop ?? prev?.scrollTop ?? 0,
      readSeq: patch.readSeq ?? prev?.readSeq ?? 0,
      updatedAt: Date.now(),
    };
    localStorage.setItem(skeletonKey(projectId), JSON.stringify(next));
  } catch {
    // 容量满/禁用 → 静默降级为不缓存（可重建原则，不是错误）
  }
}

export function clearSkeleton(projectId: string): void {
  try {
    localStorage.removeItem(skeletonKey(projectId));
  } catch {
    // 忽略：清理失败无实质影响
  }
}

/** 读取全部骨架（启动预热用）：返回骨架 Map，天然跳过损坏/版本不符条目。 */
export function loadAllSkeletons(): Map<string, SessionSkeleton> {
  const result = new Map<string, SessionSkeleton>();
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(KEY_PREFIX)) continue;
      const projectId = key.slice(KEY_PREFIX.length);
      if (!projectId) continue;
      const sk = loadSkeleton(projectId);
      if (sk) result.set(projectId, sk);
    }
  } catch {
    // 读取失败 → 空骨架（走全量快照兜底）
  }
  return result;
}
