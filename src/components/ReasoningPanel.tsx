/**
 * ReasoningPanel.tsx — [v1.0.23.3] 推理折叠面板（照 apple 参考）。
 *
 * 三部分：trigger 行（绿点 + 「推理过程」+ 副标题 + chevron）、折叠面板
 * （grid-template-rows 1fr↔0fr 动画）、滚动区（max-height 190px + 左侧竖线）。
 *
 * 两处复用：
 *   - StreamBubble（流式期间）：live=true，文本实时追加 + 自动滚底
 *   - MessageBubble（落定后）：live=false，默认展开（照参考 aria-expanded="true"）
 *
 * [v1.0.23.4 修订] 流式期间改为「21st thinking-reasoning」式推理流：
 *   视口无滚动条（overflow hidden + 上下渐变遮罩），内容整体 translateY 上推
 *   （transition .56s），新段落从底部淡入（0.42s 纯淡入），最老的段落自然滑出
 *   视口顶部——视觉 = 保持约 5 条、新进老出、始终可见最新。推理结束落定后
 *   恢复整体滚动条（overflow auto），可上下滑动查看完整推理。
 */

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Markdown } from './markdown';
import { useSessionActive } from './sessionActiveContext';

export interface ReasoningPanelProps {
  /** 完整/累积中的推理文本。 */
  text: string;
  /** 思考耗时（秒）；流式期间 undefined → 副标题省略前半。 */
  seconds?: number;
  /** 流式模式：文本变化自动滚到底部。 */
  live?: boolean;
  /** [v1.0.24.4-r13] 初始即展开（派卡接力：定格推理面板保持展开做收起动画）。 */
  initiallyExpanded?: boolean;
}

/**
 * 段落级显示（[v1.0.23.3 修订]）：
 *   流式期间只显示「已确认完成」的段落——最后一段视为还在累积，等下一段
 *   出现或流式结束（live=false）再显示。每个 <p> 出现时即完整，不逐字蹦。
 */
function visibleParagraphs(text: string, live: boolean): string[] {
  const paras = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!live) return paras;
  return paras.slice(0, -1);
}

/* ── [v1.0.23.7] morphing-infinity 指示器（替换原 thinking-dot）────
 * 来源：21st.dev @loading-ui/components/morphing-infinity。原组件用
 * motion 动画 path 的 d 属性（圆 → ∞ → 圆反向 → ∞ → 圆，5s 循环）。
 * 这里用原生 SVG SMIL <animate attributeName="d"> 实现——三条 path
 * 命令结构完全一致（M + 4×C + Z），可平滑插值，零依赖（不引入 motion）。
 * 动画参数照抄原版：dur 5s、easeInOut（keySplines 等价
 * cubic-bezier(0.42,0,0.58,1)）、times 0/.25/.5/.75/1。
 * [v1.0.23.7] 只在流式期间（live）渲染——推理完成后动画自动消失。 */
const MORPH_CIRCLE =
  'M 12 8 C 14.21 8 16 9.79 16 12 C 16 14.21 14.21 16 12 16 C 9.79 16 8 14.21 8 12 C 8 9.79 9.79 8 12 8 Z';
const MORPH_INFINITY =
  'M 12 12 C 14 8.5 19 8.5 19 12 C 19 15.5 14 15.5 12 12 C 10 8.5 5 8.5 5 12 C 5 15.5 10 15.5 12 12 Z';
const MORPH_CIRCLE_BACK =
  'M 12 16 C 14.21 16 16 14.21 16 12 C 16 9.79 14.21 8 12 8 C 9.79 8 8 9.79 8 12 C 8 14.21 9.79 16 12 16 Z';

export function ThinkingDot(): React.JSX.Element {
  return (
    <svg
      className="thinking-dot"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role="status"
      aria-hidden="true"
    >
      <path d={MORPH_CIRCLE}>
        <animate
          attributeName="d"
          values={`${MORPH_CIRCLE};${MORPH_INFINITY};${MORPH_CIRCLE_BACK};${MORPH_INFINITY};${MORPH_CIRCLE}`}
          dur="5s"
          repeatCount="indefinite"
          calcMode="spline"
          keyTimes="0;0.25;0.5;0.75;1"
          keySplines="0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1"
        />
      </path>
    </svg>
  );
}

/* ── [v1.0.23.7] 思考耗时人性化格式化 ─────────────────────────
 * <60s → "X 秒"；<1h → "X 分 Y 秒"（441.2s → "7 分 21 秒"）；
 * ≥1h → "X 小时 Y 分 Z 秒"。用户要求：几百几千秒不许直接显示。 */
function formatDuration(seconds: number, zh: boolean): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return zh ? `${total} 秒` : `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m < 60) return zh ? `${m} 分 ${s} 秒` : `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return zh ? `${h} 小时 ${mm} 分 ${s} 秒` : `${h}h ${mm}m ${s}s`;
}
export const ReasoningPanel: React.FC<ReasoningPanelProps> = ({ text, seconds, live = false, initiallyExpanded = false }) => {
  const active = useSessionActive();
  const { t, i18n } = useTranslation();
  // [v1.0.23.7] 初始 = live：流式期间展开显示推理；落定（live=false）折叠。
  //   effect 兜底：同一实例 live 从 true→false 时自动折叠（正式回复出现，
  //   推理窗口收起）；用户手动展开后 live 不变 → 不会被强制折回。
  // [v1.0.24.3] ★ expanded（视觉状态）与 mounted（DOM 挂载）分离：
  //   折叠不能立即卸载正文——CSS 的 grid-template-rows 1fr→0fr 动画需要子元素
  //   在场才有东西可收缩，立即卸载 = 动画瞬间完成（用户实测「瞬间折叠无 morph」）。
  //   折叠时先保留 DOM 播完 0.56s 收缩动画，再延迟卸载（零 DOM 防布局风暴优化保留）。
  // [v1.0.24.4-r13] initiallyExpanded：派卡接力场景（relay 收起动画）已定格的推理
  //   面板必须保持展开态做收起动画——否则定格瞬间折叠成小气泡（441→119px）再收起，
  //   且高度突变污染高度表（卡片与用户消息重叠的根因之一）。
  const [expanded, setExpanded] = useState(live || initiallyExpanded);
  const [mounted, setMounted] = useState(live || initiallyExpanded);
  useEffect(() => {
    if (!live && !initiallyExpanded) setExpanded(false);
  }, [live, initiallyExpanded]);
  // [v1.0.24.4-r14] initiallyExpanded **变化**时也强制展开（派卡接力：推理定格后
  //   组件已挂载（折叠态），relay 建立才传 initiallyExpanded——useState 初始值
  //   不随 prop 变化，必须 effect 强制展开，否则气泡停在折叠小条）。
  useEffect(() => {
    if (initiallyExpanded) setExpanded(true);
  }, [initiallyExpanded]);
  // 折叠 → 延迟卸载（等 grid 收缩动画播完）；展开 → 立即挂载。
  useEffect(() => {
    if (expanded) {
      setMounted(true);
      return undefined;
    }
    const timer = setTimeout(() => setMounted(false), 620); // grid 0.56s + 余量
    return () => clearTimeout(timer);
  }, [expanded]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  // [v1.0.23.4] 流式队列上推量（负值 = 内容向上移，让最新段落贴在视口底部）
  const [shift, setShift] = useState(0);
  // [v1.0.23.6] 渲染节流：流式 delta 高频到达（每秒几十次），每次都全量渲染
  //   70K 字符的 Markdown 会把主线程拖垮（点击/滚动/头像全卡）。rAF 合并：
  //   每帧最多渲染一次，帧间到达的 delta 全部合并——视觉无感（推理动画按帧走）。
  const [displayText, setDisplayText] = useState(text);
  const pendingTextRef = useRef<string | null>(null);
  const renderRafRef = useRef(0);
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不做流式渲染节流（不渲染 delta）
    if (!active) return;
    pendingTextRef.current = text;
    if (renderRafRef.current) return;
    renderRafRef.current = requestAnimationFrame(() => {
      renderRafRef.current = 0;
      const next = pendingTextRef.current;
      pendingTextRef.current = null;
      if (next !== null) setDisplayText(next);
    });
  }, [text, active]);
  // [v1.0.23.6] 高度 morph：流式内容增长时 scroll 高度随内容瞬间跳变 → bubble 被
  //   突然撑开（顿挫感）。ResizeObserver 把 stream 高度同步为 scroll 的显式像素高度，
  //   CSS transition 让高度慢速平滑过渡（用户视觉舒适第一，动画速率优先于内容速率）。
  //   [v1.0.24.3] ★ 生命周期必须跟 mounted 走：折叠动画期间 DOM 还在（延迟卸载），
  //   展开后新 DOM 挂载——若 effect 只依赖 [live] 不会重跑 → 新 DOM 无人观察 →
  //   panelH 卡在折叠前的旧值（刚开始推理时内容少，旧值只有几十 px）→ 面板被
  //   style.height 钉死在窄窄一行。折叠卸载时重置 panelH、展开/重挂载时重新测量。
  const [panelH, setPanelH] = useState<number | undefined>(undefined);
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不建 RO（恢复时 effect 重跑自然重建）
    if (!active) return;
    if (!live || !mounted) {
      setPanelH(undefined);
      return undefined;
    }
    const st = streamRef.current;
    const sc = scrollRef.current;
    if (!st || !sc) return undefined;
    const ro = new ResizeObserver(() => {
      setPanelH(Math.min(st.offsetHeight, 300));
    });
    ro.observe(st);
    return () => ro.disconnect();
  }, [live, mounted, active]);

  // [v1.0.23.4] 流式期间：内容超高时把 stream 整体上推（参考 21st thinking-reasoning）。
  //   react-markdown 异步渲染 → 跨两帧量高度。落定后归零（显示顶部，滚动条接管）。
  useEffect(() => {
    // [v1.0.24.6-P0] 隐藏会话停摆：不做双 rAF 测量
    if (!active) return;
    if (!live) {
      setShift(0);
      return undefined;
    }
    const sc = scrollRef.current;
    const st = streamRef.current;
    if (!sc || !st) return undefined;
    let raf1 = 0;
    let raf2 = 0;
    const measure = (): void => {
      const viewH = sc.clientHeight;
      const contentH = st.offsetHeight;
      setShift(contentH > viewH ? viewH - contentH : 0);
    };
    raf1 = requestAnimationFrame(() => {
      measure();
      raf2 = requestAnimationFrame(measure);
    });
    return () => {
      cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
    };
  }, [text, live, active]);

  const sub = seconds !== undefined
    ? t('reasoning.sub.sec', { duration: formatDuration(seconds, i18n.language.startsWith('zh')) })
    : t('reasoning.sub.live');
  const title = live ? t('reasoning.title.live') : t('reasoning.title');

  return (
    <section
      className={'reasoning' + (expanded ? '' : ' collapsed')}
      aria-label={title}
    >
      <button
        className="reasoning-trigger"
        type="button"
        aria-expanded={expanded}
        aria-controls="reasoningPanel"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="reasoning-left">
          {live && <ThinkingDot />}
          <span>
            <span className="reasoning-title">{title}</span>
            <span className="reasoning-sub">{sub}</span>
          </span>
        </span>
        <span className="chev" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
            <path
              d="m7 10 5 5 5-5"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>

      <div className="reasoning-panel" id="reasoningPanel">
        {/*
          [v1.0.23.5_2] ★ 折叠态不挂载正文：grid-template-rows:0fr 只是视觉折叠，
          正文仍在 DOM 且参与布局——窗口 resize 时 3.7 万像素推理全文被全量重排
          （布局风暴 → 系统卡死，审计 01）。折叠 → 正文零 DOM；展开才渲染。
          流式（live）路径 expanded 恒 true，不受影响。
          [v1.0.24.3] 改为 mounted（延迟卸载）：折叠先播 0.56s 收缩动画（DOM 保留），
          动画结束才卸载正文——grid 0fr 动画需要子元素在场才有 morph 缩回效果，
          立即卸载会瞬间折叠（用户实测无变形过程）。
        */}
        {mounted && (
        <div className="reasoning-clip">
          {/* [v1.0.23.4] live：overflow hidden 无滚动条 + 渐变遮罩；落定：整体滚动条 */}
          <div
            className={'reasoning-scroll' + (live ? ' live' : '')}
            ref={scrollRef}
            style={live && panelH !== undefined ? { height: panelH } : undefined}
          >
            {/* [v1.0.23.4] stream wrapper：流式队列上推动画载体 */}
            <div
              className="reasoning-stream"
              ref={streamRef}
              style={{ transform: live && shift < 0 ? `translateY(${shift}px)` : undefined }}
            >
              {/* [v1.0.23.3 修订] 段落级 Markdown 渲染：段完整才显示；格式（加粗/列表）不再裸露
                  [v1.0.23.6] 用 rAF 节流后的 displayText 渲染（每帧最多一次全量） */}
              {visibleParagraphs(displayText, live).map((para, index) => (
                <Markdown key={index} text={para} />
              ))}
            </div>
          </div>
        </div>
        )}
      </div>
    </section>
  );
};

export default ReasoningPanel;
