/**
 * virtualList.ts — [v1.0.23.4] 消息列表虚拟化核心（纯逻辑，无 React DOM 依赖，可单测）
 *
 * 三件套：
 *   1. buildRowIndex  —— items → 行索引（幽灵标记 + 最近可见邻居预计算，O(n) 一次构建）
 *   2. HeightStore    —— 行高缓存（估算 → 实测校正 → 增量更新前缀和）
 *   3. computeWindow  —— scrollTop + clientHeight → 渲染窗口 [start, end]（二分 + overscan + 流式固定行）
 *
 * 设计要点（见架构设计.md §3）：
 *   · 行高缓存 key = 行语义 key（itemKeyOf）；未测量行用估算值
 *   · offsets 前缀和：offsets[i] = 第 i 行顶部的 y 坐标（绝对定位用）
 *   · 流式行强制常驻窗口（morph 铁律：同一 DOM 节点不跳闪）
 *   · 幽灵空气泡 = 高度 0 的占位行（不渲染 DOM，但分组/分隔线计算仍引用它）
 *   · prevVisible/nextVisible 预计算：把原 prevRendered/nextRendered 的 O(n²) 扫描
 *     变成 O(n) 一次构建、行渲染时 O(1) 取用
 */

/** 幽灵判定：与 ChatStream.willRender 同语义（空文本 & 非流式 & 无文件 的 agent 气泡不渲染）。 */
export function isGhostRow(item: { kind?: string; streaming?: boolean; text?: string; files?: unknown[] } | null | undefined): boolean {
  return item?.kind === 'agent'
    && !item.streaming
    && !(item.text && item.text.trim())
    && !(item.files && item.files.length > 0);
}

/** 行索引条目。 */
export interface VRow {
  /** 行语义 key（itemKeyOf(item, index)）——data-ik/跳转/多选共用。 */
  ik: string;
  /** React 外层稳定 key（原 ChatStream outerKey 语义：cmid/cardId/i{index}，流式定格不漂移）。 */
  reactKey: string;
  /** 原始 items 下标。 */
  itemIndex: number;
  /** 幽灵空气泡：不渲染 DOM，高度 0，仅占位语义。 */
  ghost: boolean;
  /** 上一个可见行下标（非自身；开头/无则 -1）——grouped/分隔线判定用「上一条可见消息」。 */
  prevVisible: number;
  /** 下一个可见行下标（非自身；末尾/无则 -1）——tail 判定用「下一条可见消息」。 */
  nextVisible: number;
  /** 流式行：强制常驻窗口。 */
  streaming: boolean;
  /** 是否消息行（user/agent）——分隔线候选。 */
  isMsg: boolean;
}

export interface BuildIndexOpts {
  /** 行语义 key 生成器：itemKeyOf(item, index)。 */
  keyOf: (item: unknown, index: number) => string;
  /** React 外层稳定 key 生成器（原 outerKey 语义）。 */
  reactKeyOf: (item: unknown, index: number) => string;
}

/**
 * 构建行索引。
 * - 幽灵行：ghost=true（不渲染，高 0），但仍占数组位
 * - prevVisible/nextVisible：可见邻居预计算（O(n) 一次构建，消除 O(n²) 扫描）
 */
export function buildRowIndex(
  items: unknown[],
  opts: BuildIndexOpts,
): VRow[] {
  const { keyOf, reactKeyOf } = opts;
  const n = items.length;
  const rows: VRow[] = new Array(n);

  // pass 1：幽灵判定 + prevVisible（正向扫）
  // [v1.0.23.5] prevVisible = **上一个可见行**（非自身；开头 -1）。
  //   旧实现可见行返回自身 → ChatStream 里 grouped = key === senderKey(自身) 永远 true
  //   → 头像/发送者名/时间分隔线全部消失。此修复恢复「上一条可见消息」语义。
  let lastVisible = -1;
  for (let i = 0; i < n; i++) {
    const item = items[i] as { kind?: string; streaming?: boolean; text?: string; files?: unknown[] } | undefined;
    const ghost = isGhostRow(item);
    rows[i] = {
      ik: keyOf(item, i),
      reactKey: reactKeyOf(item, i),
      itemIndex: i,
      ghost,
      prevVisible: lastVisible,
      nextVisible: -1,
      streaming: item?.streaming === true,
      isMsg: item?.kind === 'user' || item?.kind === 'agent',
    };
    if (!ghost) lastVisible = i;
  }
  // pass 2：nextVisible（反向扫；非自身，末尾 -1）
  let nextVisible = -1;
  for (let i = n - 1; i >= 0; i--) {
    const r = rows[i]!;
    r.nextVisible = nextVisible;
    if (!r.ghost) nextVisible = i;
  }
  return rows;
}

/** 未测量行的估算高度（气泡平均高度）。 */
export const EST_ROW_H = 60;

/** 行基础高度：幽灵空气泡 = 0（不渲染不占位，原始布局无此空间），可见行 = 估算值。 */
export function baseHeightOf(row: VRow): number {
  return row.ghost ? 0 : EST_ROW_H;
}

/**
 * 行高缓存 + 前缀和。
 * 生命周期：projectId 变化 / 快照整表替换时 reset()（key 已失效）。
 * 前缀和全量重建（O(n)）：幽灵行占位 0，实测高度按 ik 保留。
 */
export class HeightStore {
  private heights = new Map<string, number>();
  private offsets: number[] = [];

  /** 总行数。 */
  private n = 0;

  /** [v1.0.24.4-r12] relay 动画保护集：动画期间 rebuild 不得删除这些 ik 的实测高度。
   *   幽灵行删除（v1.0.23.5-r5）与 relay 的 rAF measure 竞态：rebuild 删掉气泡行
   *   高度后，onFrame 的 measure 遇到 prev=undefined → 用估算 60 算 delta（bh-60）
   *   → 每帧污染前缀和 → 终态卡片上移 60px 与用户消息重叠。保护后 prev 稳定。 */
  private protectedIk = new Set<string>();

  /** [r12] 保护一个 ik（relay 建立对时调用）。 */
  protect(ik: string): void {
    this.protectedIk.add(ik);
  }

  /** [r12] 解除保护（relay 落定后调用）。 */
  unprotect(ik: string): void {
    this.protectedIk.delete(ik);
  }

  /** [v1.0.24.7-P0-3] 幽灵行 ik 集（rebuild 时刷新）：幽灵行不渲染、占位 0。
   *   首次 measure 的 delta 基准必须用 0 而非 EST_ROW_H（60）——否则 relay 兜底
   *   落定对幽灵气泡行 measure(0) 时 prev=undefined → delta=-60 → 后续前缀和
   *   全部 -60 → 卡片行上移 60px 顶到用户消息（多群并发 4 群全中实锤）。 */
  private ghostIks = new Set<string>();

  /** 全量重建前缀和（保留实测高度；幽灵行强制 0 占位）。 */
  rebuild(rows: VRow[]): void {
    this.n = rows.length;
    this.offsets = new Array(this.n + 1);
    this.offsets[0] = 0;
    // [v1.0.24.7-P0-3] 刷新幽灵行集（measure 的 delta 基准用）
    this.ghostIks = new Set<string>();
    for (let i = 0; i < this.n; i++) {
      const row = rows[i]!;
      if (row.ghost) this.ghostIks.add(row.ik);
      // [v1.0.23.5-r5] 幽灵行强制 0：streaming 中间态（推理↔落定）行可能短暂变回幽灵，
      //   heights 里残留实测值（如 438px）会让它占位 → 推理过程中的巨大空白。
      //   直接清除残留，杜绝「曾渲染→变幽灵→占位」的空白源。
      //   [v1.0.24.4-r12] relay 动画中的行（protected）例外：rAF 独占驱动中，删除
      //   会触发 prev=undefined 的估算 delta 污染（见 protect 注释）。
      //   [v1.0.24.4-r15] protected 行**按非幽灵处理**（用实测高度占位）：relay 气泡行
      //   是幽灵行，但动画期间 rAF 逐帧 measure 真实动画高度。若 rebuild 仍按幽灵强制 0，
      //   动画中任何一次 rows 变化（streaming 新消息入列）触发 rebuild 都会抹掉当前动画
      //   高度 → 前缀和塌缩 → 后续所有行上移 → 落定卡片盖住上方气泡（实测上移 224px）。
      //   保护期用 heights（rAF 维护的当前值），与 measure 的增量基准（prev ?? EST_ROW_H）
      //   完全一致 → rebuild 与 rAF 不再互相抵消。落定 unprotect 后恢复幽灵清零语义。
      const ghost = row.ghost && !this.protectedIk.has(row.ik);
      if (ghost) this.heights.delete(row.ik);
      this.offsets[i + 1] = (this.offsets[i] ?? 0) + (ghost ? 0 : (this.heights.get(row.ik) ?? EST_ROW_H));
    }
  }

  /** 重置（切群/快照重建时调用）：实测缓存 key 已失效 → 清空后重建。 */
  reset(rows: VRow[]): void {
    this.heights.clear();
    this.rebuild(rows);
  }

  /**
   * [v1.0.23.6] 导出实测高度（切群缓存用）。
   * 返回当前所有已实测的 ik→height；估算值不导出（估算随时可重建）。
   */
  exportHeights(): Map<string, number> {
    return new Map(this.heights);
  }

  /**
   * [v1.0.23.6] 导入另一群的实测高度缓存（切群恢复用）。
   * 清空当前缓存 → 合并导入 → 重建前缀和。行内容若已变化，RO 实测会增量校正。
   */
  importHeights(cache: Map<string, number>, rows: VRow[]): void {
    this.heights = new Map(cache);
    this.rebuild(rows);
  }

  /** 行数/内容变化时重建（流式行在尾部，绝大多数 ik 仍有效 → 实测高度保留）。 */
  resize(rows: VRow[]): void {
    this.rebuild(rows);
  }

  get rowCount(): number {
    return this.n;
  }

  /** 行顶部 y 坐标（绝对定位用）。 */
  topOf(index: number): number {
    return index >= 0 && index < this.offsets.length ? (this.offsets[index] ?? 0) : 0;
  }

  /** 总高度（估算 + 实测混合；滚动容器 scrollHeight 的替代）。 */
  get totalHeight(): number {
    return this.n > 0 ? (this.offsets[this.n] ?? 0) : 0;
  }

  /** 记录实测高度，增量更新后续前缀和（流式行几乎总在尾部，O(尾部行数)）。 */
  measure(ik: string, index: number, height: number): void {
    if (index < 0 || index >= this.n) return;
    if (height < 0 || !Number.isFinite(height)) return;
    const prev = this.heights.get(ik);
    if (prev === height) return;
    this.heights.set(ik, height);
    // [v1.0.24.7-P0-3] delta 基准幽灵感知：幽灵行首次实测（prev=undefined）基准用 0
    //   而非 EST_ROW_H（60）。否则 relay 兜底落定对幽灵气泡行 measure(0) 时
    //   delta=0-60=-60 → 前缀和污染 → 卡片上移 60px 与用户消息重叠（4 群实锤）。
    //   非幽灵行维持 EST_ROW_H 基准（rebuild 占位语义一致）。
    const base = prev ?? (this.ghostIks.has(ik) ? 0 : EST_ROW_H);
    const delta = height - base;
    for (let i = index + 1; i <= this.n; i++) {
      this.offsets[i] = (this.offsets[i] ?? 0) + delta;
    }
  }

  /** 二分：最后一个 offsets[i] <= target 的行下标（lowerBound）。 */
  indexAt(target: number): number {
    let lo = 0;
    let hi = this.n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if ((this.offsets[mid] ?? 0) <= target) lo = mid + 1;
      else hi = mid;
    }
    return Math.max(0, lo - 1);
  }
}

export interface WindowRange {
  start: number;
  end: number;   // 含
}

/**
 * 计算渲染窗口。
 * @param scrollTop 滚动位置
 * @param clientHeight 可视高度
 * @param store 高度缓存
 * @param rows 行索引（取 streaming 行做固定行）
 * @param overscan 上下缓冲行数
 */
export function computeWindow(
  scrollTop: number,
  clientHeight: number,
  store: HeightStore,
  rows: VRow[],
  overscan = 15,
): WindowRange {
  const n = rows.length;
  if (n === 0) return { start: 0, end: -1 };
  let start = store.indexAt(Math.max(0, scrollTop));
  let end = store.indexAt(scrollTop + Math.max(1, clientHeight));
  start = Math.max(0, start - overscan);
  end = Math.min(n - 1, end + overscan);
  // 流式行强制常驻
  for (let i = 0; i < n; i++) {
    if (rows[i]!.streaming) {
      if (i < start) start = i;
      if (i > end) end = i;
    }
  }
  return { start, end };
}

/** 幽灵行不渲染（返回 false 的行跳过 DOM，但占索引位）。 */
export function shouldRenderRow(row: VRow): boolean {
  return !row.ghost;
}
