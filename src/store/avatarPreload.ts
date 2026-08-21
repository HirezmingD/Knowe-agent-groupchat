/**
 * avatarPreload.ts — 头像图片预加载缓存（纯逻辑模块，无 React 依赖）。
 *
 * [v1.0.39-B] 从 Avatar.tsx 抽出：store（启动 populate 后立即预载）与
 * Avatar 组件（渲染时查缓存）共享**同一**缓存池，单一状态源。
 *
 * 机制：
 * · preloadAvatar(url)：首次调用发起 Image 加载并登记状态；重复调用
 *   要么同步返回终态（ok/err），要么挂进 waiters 等同一个 Promise。
 * · preloadAvatarBulk(urls)：启动时批量预载——渲染前把全部头像 URL
 *   灌进缓存池，组件挂载时在 paint 前命中 loading/ok，字形阶段被
 *   压缩到图片真实加载时间（不再出现"首帧全字形等几秒"）。
 *
 * 关键设计（沿用 v1.0.23.6）：同一 src 页面生命周期内只加载一次；
 * drag in dev 模式 no-cache 的图片经 Chromium 磁盘缓存二次启动加速。
 */

/** src → 加载状态 + 等待该 src 完成的所有挂载方 */
export type AvatarCacheEntry = {
  state: 'loading' | 'ok' | 'err';
  waiters: Set<() => void>;
};

export const avatarCache = new Map<string, AvatarCacheEntry>();

/**
 * 预加载一张头像。返回 'ok' | 'err'（供调用方决定兜底）。
 * 已加载完成的图立刻 resolve；加载中的图挂进 waiters 等同一结果。
 */
export function preloadAvatar(src: string): Promise<'ok' | 'err'> {
  const hit = avatarCache.get(src);
  if (hit) {
    const st = hit.state;
    if (st !== 'loading') return Promise.resolve(st);
    return new Promise((res) => hit.waiters.add(() => res(hit.state as 'ok' | 'err')));
  }
  const entry: AvatarCacheEntry = { state: 'loading', waiters: new Set() };
  avatarCache.set(src, entry);
  return new Promise((res) => {
    const img = new Image();
    img.onload = () => {
      entry.state = 'ok';
      entry.waiters.forEach((w) => w());
      entry.waiters.clear();
      res('ok');
    };
    img.onerror = () => {
      entry.state = 'err';
      entry.waiters.forEach((w) => w());
      entry.waiters.clear();
      res('err');
    };
    img.src = src;
  });
}

/**
 * 批量预载（启动秒出用）。去重后逐张发起；不阻塞、不等待。
 * 组件渲染时命中 avatarCache 的 loading 状态 → 等同一 Promise，
 * 图片一到 waiters 统一放行 → 字形阶段只存在于图片真实加载期间。
 */
export function preloadAvatarBulk(urls: string[] | undefined | null): void {
  if (!urls || urls.length === 0) return;
  const seen = new Set<string>();
  for (const u of urls) {
    if (!u || seen.has(u)) continue;
    seen.add(u);
    void preloadAvatar(u);
  }
}

/** 清空缓存池（测试用）。 */
export function resetAvatarPreload(): void {
  avatarCache.clear();
}

/** 查询某 src 的缓存状态（组件渲染用，避免直接摸 Map）。 */
export function avatarCacheState(src: string): 'loading' | 'ok' | 'err' | undefined {
  return avatarCache.get(src)?.state;
}