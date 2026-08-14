// src/components/TokenUsagePanel.tsx
// [v1.0.20.1-M3] Token 消耗统计抽屉（全量重写）。
//
// 规格（REPORT_M3.md + UI设计规范.md）：
// - 布局骨架照 DeepSeek usage 面板：筛选栏 → 统计卡×3 → 趋势图 → 明细表（双 tab）→ 底部时效说明
// - 数据全部由后端按范围聚合（token_usage_req 带 start_ts/end_ts），前端不做本地过滤
// - 动效纪律（Emil）：抽屉 scale(0.95)→1 320ms ease-out origin 右上；出场反向 200ms 快于入场；
//   卡片 stagger 0/40/80ms；柱状图 scaleY 420ms ease-inout 每柱 30ms stagger；
//   命中率 scaleX；弹层 scale(0.97)→1 200ms；数字滚动 rAF 200ms；按压 scale(0.97)；reduced-motion 降级
// - 全部视觉走 Knowe 设计令牌 var()，深/浅双主题零额外工作

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import {
  type TokenRange,
  type TokenUsageAgent,
  type TokenUsageDaily,
  type TokenUsageModel,
  useTokenUsageStore,
} from '../store/tokenUsage';
import { RangeCalendar, localDateKey } from './RangeCalendar';

const NUMBER_FORMAT = new Intl.NumberFormat('zh-CN');

/** [v1.0.34-M4-v2] 字符→词元估算系数：1 词元 ≈ 1.6 字符（中文为主，代码/英文
 *  混排 ±20% 误差，用户拍板 B 方案：前端近似换算并标注「约」，不做后端 tokenizer）。 */
const ZH_TO_TOKEN_RATIO = 1.6;

type DetailTab = 'model' | 'agent';

/** 拖拽手柄的最小面板高度（px）：低于此值内容无可用视口。 */
const MIN_PANEL_H = 200;

function formatTokens(value: number): string {
  return NUMBER_FORMAT.format(Math.max(0, Math.trunc(value || 0)));
}

function formatCompact(value: number): string {
  // 全量数字展示（不简写 k/M），如 968,828,341；长度自适应由调用方处理。
  return NUMBER_FORMAT.format(Math.max(0, Math.trunc(value || 0)));
}

/** [v1.0.34-M4-v2] 累计大数简写：中文 千/万/亿/兆，英文 K/M/B/T（按当前语言）。
 *  阈值：zh ≥1e3 千、≥1e4 万、≥1e8 亿、≥1e12 兆；en ≥1e3 K、≥1e6 M、≥1e9 B、≥1e12 T；
 *  小数字保留全量。 */
function formatZhCompact(value: number): string {
  const n = Math.max(0, Math.trunc(value || 0));
  const trim = (v: number): string => {
    const s = v.toFixed(1);
    return s.endsWith('.0') ? s.slice(0, -2) : s;
  };
  if (i18n.language.startsWith('zh')) {
    if (n >= 1e12) return `${trim(n / 1e12)}兆`;
    if (n >= 1e8) return `${trim(n / 1e8)}亿`;
    if (n >= 1e4) return `${trim(n / 1e4)}万`;
    if (n >= 1e3) return `${trim(n / 1e3)}千`;
  } else {
    if (n >= 1e12) return `${trim(n / 1e12)}T`;
    if (n >= 1e9) return `${trim(n / 1e9)}B`;
    if (n >= 1e6) return `${trim(n / 1e6)}M`;
    if (n >= 1e3) return `${trim(n / 1e3)}K`;
  }
  return NUMBER_FORMAT.format(n);
}

function formatAxisCompact(value: number): string {
  // 纵轴刻度专用：千/万/亿简写（k/w/亿），刻度不宜过长。
  const number = Math.max(0, Math.trunc(value || 0));
  if (number >= 100_000_000) {
    const v = number / 100_000_000;
    return i18n.t('token.usage.panel.yiUnit', { n: v >= 100 ? v.toFixed(0) : v.toFixed(1) });
  }
  if (number >= 10_000) {
    const v = number / 10_000;
    return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)}w`;
  }
  if (number >= 1_000) {
    const v = number / 1_000;
    return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)}k`;
  }
  return NUMBER_FORMAT.format(number);
}

function formatCny(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return `¥${value.toFixed(2)}`;
}

function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return `$${value.toFixed(2)}`;
}

/** 数字滚动：rAF 计数 200ms ease-out；reduced-motion 直接终值。 */
function useCountUp(target: number, duration = 200): number {
  const [value, setValue] = useState(target);
  const fromRef = useRef(target);
  useEffect(() => {
    const from = fromRef.current;
    if (from === target) {
      setValue(target);
      return;
    }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      fromRef.current = target;
      setValue(target);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const tick = (now: number): void => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(from + (target - from) * eased));
      if (progress < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        fromRef.current = target;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      fromRef.current = target;
    };
  }, [target, duration]);
  return value;
}

const PRESETS: { kind: TokenRange['kind']; label: string }[] = [
  { kind: 'today', label: 'token.usage.panel.01' },
  { kind: '7d', label: 'token.usage.panel.23' },
  { kind: '30d', label: 'token.usage.panel.22' },
  { kind: 'total', label: 'token.usage.panel.04' },
];

interface StatCardProps {
  label: string;
  value: string;
  accent?: boolean;
  sub?: React.ReactNode;
  delay: number;
  className?: string;
}

const StatCard: React.FC<StatCardProps> = ({ label, value, accent, sub, delay, className }) => {
  const valueRef = useRef<HTMLDivElement>(null);
  const [fontSize, setFontSize] = useState(28);
  useEffect(() => {
    const el = valueRef.current;
    if (!el) return;
    // [v1.0.34-M4] 数字位数动态适配：字号 = clamp(11px, 卡片宽度 ÷ (位数 × 单字宽系数), 28px)。
    //   替换旧的「scrollWidth 溢出 → 粗暴缩到 0.55」：按位数精确计算，拉宽即恢复大字号。
    //   单字宽系数 0.62（tabular-nums 半角数字 + 千分位逗号的实际平均字宽）。
    const fit = (): void => {
      const digits = value.replace(/[^0-9]/g, '').length;
      const width = el.clientWidth;
      if (digits <= 0 || width <= 0) {
        setFontSize(28);
        return;
      }
      const computed = Math.floor(width / (digits * 0.62));
      setFontSize(Math.max(11, Math.min(28, computed)));
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, [value]);
  return (
    <div className={'tk-card' + (className ? ` ${className}` : '')} style={{ animationDelay: `${delay}ms` }}>
      <div className="tk-card-label">{label}</div>
      <div
        ref={valueRef}
        className={'tk-card-value' + (accent ? ' accent' : '')}
        style={{ fontSize: `${fontSize}px`, lineHeight: `${fontSize}px` }}
      >{value}</div>
      {sub && <div className="tk-card-sub">{sub}</div>}
    </div>
  );
};

interface TrendChartProps {
  rows: TokenUsageDaily[];
}

type TrendMode = 'stack' | 'area';

const TREND_W = 640;
const TREND_H = 132;
const AXIS_W = 46;
const PLOT_TOP = 6;
const PLOT_BOTTOM = TREND_H - 4;
const PLOT_H = PLOT_BOTTOM - PLOT_TOP;

interface TrendColumn {
  centerX: number;
  topY: number;
  hasData: boolean;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  hit?: number;
  miss?: number;
  output?: number;
  hitH?: number;
  missH?: number;
  outH?: number;
}

const TrendChart: React.FC<TrendChartProps> = ({ rows }) => {
  const { t } = useTranslation();
  const [grown, setGrown] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setGrown(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  useEffect(() => {
    setHover(null);
  }, [rows]);

  const count = rows.length;
  // 口径：≤30 天=堆叠柱（单日=单根堆叠柱，统一）；>30 天=面积图
  const mode: TrendMode = count > 30 ? 'area' : 'stack';
  const plotW = TREND_W - AXIS_W;
  const maxTotal = Math.max(1, ...rows.map((row) => row.total_tokens));

  // 柱子群中心（viewBox 坐标）：使柱子群相对整个抽屉精确居中。
  // 抽屉 50% = 纵轴 7.1875% + 主区 92.8125% * (x/640) → x = (0.5-0.071875)/0.928125*640
  const BAR_CENTER_X = (0.5 - AXIS_W / TREND_W) / ((TREND_W - AXIS_W) / TREND_W) * TREND_W;

  // 有实际非 0 数据的行（stack 过滤 0 值日；area 保留全部时间轴）
  const dataRows = useMemo(
    () => (mode === 'stack' ? rows.filter((row) => row.total_tokens > 0) : rows),
    [rows, mode],
  );

  // 纵轴刻度 5 档（0/25/50/75/100%）
  const ticks = useMemo(() => {
    const raw = [0, 0.25, 0.5, 0.75, 1].map((f) => maxTotal * f);
    return [...new Set(raw.map((v) => Math.round(v)))].sort((a, b) => b - a);
  }, [maxTotal]);
  const grid = useMemo(() => [0, 0.25, 0.5, 0.75, 1].map((f, i) => ({
    y: PLOT_BOTTOM - f * PLOT_H,
    label: ticks[i] !== undefined ? formatAxisCompact(ticks[i]) : '',
  })), [ticks]);

  // 纵轴刻度字号：全量数字较长时自动缩小以适配列宽（下限 5.5px）
  const tickFontSize = useMemo(() => {
    const longest = grid.reduce((max, g) => Math.max(max, g.label.length), 0);
    return Math.max(5.5, Math.min(10, Math.floor((AXIS_W - 6) / Math.max(1, longest * 0.6) * 10) / 10));
  }, [grid]);

  // ── 几何：stack=非0日堆叠柱（群中心居中）；area=堆叠面积 ──
  const columns = useMemo<TrendColumn[]>(() => {
    if (mode === 'stack') {
      const n = dataRows.length;
      if (!n) return [];
      const bw = Math.max(6, Math.min(22, (plotW / n) * 0.56));
      const gap = 8;
      const totalW = bw * n + gap * (n - 1);
      const startX = BAR_CENTER_X - totalW / 2;
      return dataRows.map((row, i) => {
        const x = startX + i * (bw + gap);
        const hit = row.cache_hit_input || 0;
        const miss = row.cache_miss_input || 0;
        const output = row.output_tokens || 0;
        const hitH = (hit / maxTotal) * PLOT_H;
        const missH = (miss / maxTotal) * PLOT_H;
        const outH = (output / maxTotal) * PLOT_H;
        return {
          x, w: bw,
          hit, miss, output, hitH, missH, outH,
          hasData: true,
          centerX: x + bw / 2,
          topY: PLOT_BOTTOM - hitH - missH - outH,
        };
      });
    }
    const step = plotW / Math.max(1, count - 1);
    return rows.map((row, i) => {
      const hit = row.cache_hit_input || 0;
      const miss = row.cache_miss_input || 0;
      const output = row.output_tokens || 0;
      const outH = ((hit + miss + output) / maxTotal) * PLOT_H;
      return { hasData: row.total_tokens > 0, centerX: AXIS_W + i * step, topY: PLOT_BOTTOM - outH };
    });
  }, [rows, dataRows, mode, plotW, maxTotal, count]);

  // 面积 path（堆叠三层：命中/未命中/输出）
  const area = useMemo(() => {
    if (mode !== 'area') return null;
    const step = plotW / Math.max(1, count - 1);
    const pts = { hit: [] as number[], miss: [] as number[], out: [] as number[] };
    rows.forEach((row) => {
      const hit = row.cache_hit_input || 0;
      const miss = row.cache_miss_input || 0;
      const output = row.output_tokens || 0;
      pts.hit.push((hit / maxTotal) * PLOT_H);
      pts.miss.push(((hit + miss) / maxTotal) * PLOT_H);
      pts.out.push(((hit + miss + output) / maxTotal) * PLOT_H);
    });
    const line = (arr: number[]) =>
      arr.map((v, i) => `${i === 0 ? 'M' : 'L'}${(AXIS_W + i * step).toFixed(1)} ${(PLOT_BOTTOM - v).toFixed(1)}`).join(' ');
    const areaPath = (arr: number[]) =>
      `${line(arr)} L${(AXIS_W + (count - 1) * step).toFixed(1)} ${PLOT_BOTTOM} L${AXIS_W} ${PLOT_BOTTOM} Z`;
    return {
      hitPath: areaPath(pts.hit),
      missPath: areaPath(pts.miss),
      outPath: areaPath(pts.out),
    };
  }, [rows, mode, plotW, maxTotal, count]);

  // x 轴标签：跟随非 0 柱子的中轴；≤7 全显；更多抽 5 个
  const xLabels = useMemo(() => {
    const n = columns.length;
    if (n === 0) return [];
    if (n <= 7) return columns.map((col, i) => ({ x: col.centerX, label: dataRows[i]!.date.slice(5) }));
    const idx = [...new Set([
      0,
      Math.floor((n - 1) * 0.25),
      Math.floor((n - 1) * 0.5),
      Math.floor((n - 1) * 0.75),
      n - 1,
    ])];
    return idx.map((i) => ({ x: columns[i]!.centerX, label: dataRows[i]!.date.slice(5) }));
  }, [columns, dataRows]);

  // hover 列：磁吸到最近的非 0 柱（鼠标在柱间空白时归属最近一侧）
  const hoverCol = hover != null ? columns[Math.min(hover, columns.length - 1)] : null;
  const hoverRow = hover != null
    ? (mode === 'area' ? rows[Math.min(hover, rows.length - 1)] : dataRows[Math.min(hover, dataRows.length - 1)])
    : null;

  // 磁吸判定：鼠标 x（viewBox 坐标）→ 最近 hasData 柱。
  // 只在首尾柱子之间的区间内磁吸（相邻柱之间的空白归属最近一侧）；
  // 最外侧柱子之外的空白不触发（单根柱子两侧同样无磁吸）。
  const magnetTo = (clientX: number): void => {
    const svg = mainRef.current?.querySelector('svg');
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * TREND_W;
    const withData = columns.map((col, i) => ({ col, i })).filter((item) => item.col.hasData);
    if (!withData.length) {
      setHover(null);
      return;
    }
    const first = withData[0]!.col;
    const last = withData[withData.length - 1]!.col;
    const leftBound = first.centerX - (first.w ?? 0) / 2;
    const rightBound = last.centerX + (last.w ?? 0) / 2;
    if (x < leftBound || x > rightBound) {
      setHover(null);
      return;
    }
    let best = -1;
    let bestDist = Infinity;
    withData.forEach(({ col, i }) => {
      const dist = Math.abs(col.centerX - x);
      if (dist < bestDist) {
        bestDist = dist;
        best = i;
      }
    });
    setHover(best >= 0 ? best : null);
  };

  // tooltip 定位：全 CSS 百分比方案——left 用 %（相对 .tk-trend-main 宽度），
  // 视口/面板宽度变化时浏览器自动重排，无需 JS 重算、无需 re-render。
  // （曾用 JS scale 计算，useMemo 依赖缺失导致拉宽/拉窄后卡片沿用旧视口坐标而错位；
  //   也试过 ResizeObserver 强制重渲染，visibility:hidden 时回调被节流不可靠。）
  const tipStyle = useMemo<React.CSSProperties | null>(() => {
    if (!hoverCol) return null;
    // 柱心水平位置（容器百分比）：centerX 是 viewBox 坐标，/TREND_W 即容器比例。
    // 垂直固定图高中点：SVG 渲染高度恒 132px（.tk-trend-svg height:132px，
    // preserveAspectRatio="none" 只拉伸水平），垂直比例恒为 1。
    const top = TREND_H / 2;
    // ★ 偏移 = 柱子渲染半宽（容器百分比）+ 恒定 20px 间隙 → 卡片到柱边距离恒定。
    //   柱宽 viewBox 逻辑 w 渲染后 = w × (mainW/640) = w/640 的容器宽度——
    //   用容器百分比表达自动跟随窗口拉宽/拉窄。（area 模式无柱，半宽 0）
    //   注意 transform 的 % 是相对元素自身，容器比例只能进 left。
    const leftPct = (hoverCol.centerX / TREND_W) * 100;
    const halfWpct = ((hoverCol.w ?? 0) / 2 / TREND_W) * 100;
    const gap = 20;
    // 柱子在中线右侧 → 卡片放左侧（柱左缘 - 20px，再左移自身宽）；左侧 → 放右侧
    const placeLeft = hoverCol.centerX > TREND_W / 2;
    return {
      left: placeLeft
        ? `calc(${leftPct - halfWpct}% - ${gap}px)`
        : `calc(${leftPct + halfWpct}% + ${gap}px)`,
      top,
      transform: placeLeft
        ? 'translate3d(calc(-100% - 20px), -50%, 0)'
        : 'translate3d(0, -50%, 0)',
    };
  }, [hoverCol]);

  return (
    <div className="tk-trend">
      <div className="tk-trend-axis-y" aria-hidden="true">
        {grid.map((g) => (
          <span key={g.y} className="tk-trend-y" style={{ fontSize: `${tickFontSize}px`, lineHeight: `${tickFontSize + 2}px` }}>{g.label}</span>
        ))}
      </div>
      <div className="tk-trend-main" ref={mainRef} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${TREND_W} ${TREND_H}`} preserveAspectRatio="none" className="tk-trend-svg">
          {/* 网格线：左贴纵轴，右贴图幅 */}
          {grid.map((g) => (
            <line key={g.y} x1={AXIS_W} y1={g.y} x2={TREND_W} y2={g.y} className="tk-grid-line" />
          ))}
          {mode === 'area' && area && (
            <g className="tk-area-group">
              <path d={area.hitPath} className="tk-area-hit" />
              <path d={area.missPath} className="tk-area-miss" />
              <path d={area.outPath} className="tk-area-out" />
            </g>
          )}
          {mode === 'stack' && columns.map((col, i) => {
            const stagger = i * 30;
            const c = col as Required<TrendColumn>;
            return (
              <g key={i} className="tk-bar-group"
                style={{ transform: grown ? 'scaleY(1)' : 'scaleY(0)', transitionDelay: `${stagger}ms` }}>
                {c.hit > 0 && (
                  <rect className="tk-bar-hit" x={c.x} y={PLOT_BOTTOM - c.hitH} width={c.w} height={c.hitH} rx={2} />
                )}
                {c.miss > 0 && (
                  <rect className="tk-bar-miss" x={c.x} y={PLOT_BOTTOM - c.hitH - c.missH} width={c.w} height={c.missH} rx={2} />
                )}
                {c.output > 0 && (
                  <rect className="tk-bar-out" x={c.x} y={PLOT_BOTTOM - c.hitH - c.missH - c.outH} width={c.w} height={c.outH} rx={2} />
                )}
              </g>
            );
          })}
          {/* hover 虚线指引 */}
          {hoverCol && (
            <line x1={hoverCol.centerX} y1={PLOT_TOP} x2={hoverCol.centerX} y2={PLOT_BOTTOM} className="tk-trend-guide" />
          )}
          {/* hover 热区：整行连续磁吸（空白处归属最近柱，无跳动） */}
          <rect
            x={AXIS_W}
            y={PLOT_TOP}
            width={plotW}
            height={PLOT_H}
            fill="transparent"
            onMouseMove={(event) => magnetTo(event.clientX)}
            onMouseLeave={() => setHover(null)}
          />
        </svg>
        <div className="tk-trend-axis">
          {xLabels.map((label) => (
            <span key={label.x} className="tk-trend-x" style={{ left: `${(label.x / TREND_W) * 100}%` }}>{label.label}</span>
          ))}
        </div>
        {hoverRow && hoverCol && tipStyle && (
          <div className="tk-trend-tip" style={tipStyle}>
            <div className="tk-tip-date">{hoverRow.date}</div>
            <div className="tk-tip-body">
              <span className="tk-tip-hit"><i />{t('token.usage.panel.02')} {formatCompact(hoverRow.cache_hit_input || 0)}</span>
              <span className="tk-tip-miss"><i />{t('token.usage.panel.13')} {formatCompact(hoverRow.cache_miss_input || 0)}</span>
              <span className="tk-tip-out"><i />{t('token.usage.panel.21')} {formatCompact(hoverRow.output_tokens || 0)}</span>
            </div>
            <div className="tk-tip-total">{t('token.usage.panel.total', { n: formatCompact(hoverRow.total_tokens || 0) })}</div>
          </div>
        )}
      </div>
    </div>
  );
};

interface HitRateBarProps {
  hit: number;
  miss: number;
  delay?: number;
}

const HitRateBar: React.FC<HitRateBarProps> = ({ hit, miss, delay = 0 }) => {
  const { t } = useTranslation();
  const [grown, setGrown] = useState(false);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setGrown(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  const total = hit + miss;
  const rate = total > 0 ? hit / total : 0;
  return (
    <div
      className="tk-rate"
      role="img"
      aria-label={`${t('token.usage.panel.16')} ${Math.round(rate * 100)}%`}
    >
      <span
        className="tk-rate-fill"
        style={{
          transform: grown ? `scaleX(${rate})` : 'scaleX(0)',
          transitionDelay: `${delay}ms`,
        }}
      />
    </div>
  );
};

interface DetailRowProps {
  name: string;
  meta?: string;
  calls: number;
  hit: number;
  miss: number;
  tokens: number;
  cost: number | null;
  costNote?: string;
  delay: number;
  removed?: boolean;
}

const DetailRow: React.FC<DetailRowProps> = ({
  name,
  meta,
  calls,
  hit,
  miss,
  tokens,
  cost,
  costNote,
  delay,
  removed,
}) => {
  const { t, i18n: ui18n } = useTranslation();
  return (
  <div className="tk-row">
    <div className="tk-row-name" title={name}>
      <span className="tk-row-title">{name}</span>
      {meta && <span className="tk-row-meta">{meta}</span>}
      {removed && <span className="tk-row-removed">{t('token.usage.panel.03')}</span>}
    </div>
    <span className="tk-row-calls">{formatTokens(calls)}</span>
    <div className="tk-row-rate-cell">
      <HitRateBar hit={hit} miss={miss} delay={delay} />
      <span className="tk-row-rate-text">
        {hit + miss > 0 ? `${Math.round((hit / (hit + miss)) * 100)}%` : '--'}
      </span>
    </div>
    <span className="tk-row-tokens">{formatCompact(tokens)}</span>
    <span className="tk-row-cost" title={costNote}>
      {cost == null
        ? <span className="tk-no-price">{t('token.usage.panel.10')}</span>
        : (ui18n.language.startsWith('en') ? formatUsd(cost) : formatCny(cost))}
    </span>
  </div>
  );
};

export const TokenUsagePanel: React.FC = () => {
  const { t } = useTranslation();
  const open = useTokenUsageStore((state) => state.open);
  const projectId = useTokenUsageStore((state) => state.projectId);
  const range = useTokenUsageStore((state) => state.range);
  const data = useTokenUsageStore((state) => state.data);
  const openPanel = useTokenUsageStore((state) => state.openPanel);
  const closePanel = useTokenUsageStore((state) => state.closePanel);

  const panelRef = useRef<HTMLElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [closing, setClosing] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>('model');

  // 拖拽手柄调整面板展示视口高度（默认 null = 内容自适应，上限 72vh）。
  // 拉高上限 = 输入区（.composer-wrap）上边界，留 4px 呼吸；下限 MIN_PANEL_H。
  const [panelH, setPanelH] = useState<number | null>(null);
  const dragRef = useRef<{ startY: number; startH: number; maxH: number } | null>(null);

  const getMaxPanelH = (): number => {
    // 上限 = 输入区上边界（相对面板顶部的可用高度）。
    // tk-wrap 是 absolute top:0，但 containing block 顶部可能有偏移（如顶栏），
    // 须用 wrap 自身 rect.top 换算，否则面板底边会越过输入区。
    const wrap = wrapRef.current;
    const composer = document.querySelector<HTMLElement>('.composer-wrap');
    const limit = (composer?.getBoundingClientRect().top ?? window.innerHeight) - (wrap?.getBoundingClientRect().top ?? 0) - 4;
    return Math.max(MIN_PANEL_H, limit);
  };
  const clampPanelH = (h: number): number => Math.min(Math.max(h, MIN_PANEL_H), getMaxPanelH());

  const onResizeStart = (event: React.PointerEvent<HTMLDivElement>): void => {
    event.preventDefault();
    const wrap = wrapRef.current;
    if (!wrap) return;
    dragRef.current = { startY: event.clientY, startH: wrap.getBoundingClientRect().height, maxH: getMaxPanelH() };
    wrap.style.maxHeight = 'none';
    if (panelRef.current) panelRef.current.style.maxHeight = 'none';
    document.body.classList.add('resizing-v');

    const onMove = (ev: PointerEvent): void => {
      const drag = dragRef.current;
      if (!drag) return;
      wrap.style.height = `${clampPanelH(drag.startH + (ev.clientY - drag.startY))}px`;
    };
    const onUp = (): void => {
      dragRef.current = null;
      document.body.classList.remove('resizing-v');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      const wrap = wrapRef.current;
      if (!wrap) return;
      if (wrap.style.height) {
        setPanelH(parseFloat(wrap.style.height));
      } else {
        // 只点未拖：还原 max-height（inline 覆盖已加，需回收，恢复 72vh 默认）
        wrap.style.removeProperty('max-height');
        if (panelRef.current) panelRef.current.style.removeProperty('max-height');
        setPanelH(null);
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // 键盘可访问：上下箭头 ±20px（上限同拖拽）
  const onResizeKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    const wrap = wrapRef.current;
    if (!wrap) return;
    const base = panelH ?? wrap.getBoundingClientRect().height;
    setPanelH(clampPanelH(base + (event.key === 'ArrowUp' ? 20 : -20)));
  };

  // 关闭动画：先加 .closing（出场 400ms 滑出），动画结束再真正卸载。
  // ★ 卸载延迟必须与 CSS .tk-wrap.closing 的 animation-duration 同步（当前 400ms）——
  //   改 CSS 时长时必须同步改这里，否则动画播一半就被卸载（曾踩坑：CSS 500ms 时此处仅 200）。
  const requestClose = (): void => {
    setCustomOpen(false);
    setClosing(true);
    window.setTimeout(() => {
      closePanel();
      setClosing(false);
    }, 400);
  };

  // 点外部 / Esc 关闭；触发器按钮与面板自身不算外部。
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent): void => {
      const target = event.target as Node | null;
      if (!target) return;
      // 面板自身（含底部拖拽手柄 .tk-resize）都不算外部——手柄在 tk-panel 外，须用 wrapRef 判定
      if (wrapRef.current?.contains(target)) return;
      if (target instanceof Element && target.closest('[data-token-usage-toggle]')) return;
      requestClose();
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') requestClose();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const totals = data?.totals;
  const hasUsage = Boolean(totals?.total_calls);

  // [v1.0.34-M4] 上下文占用三数：占用率（-- 缺失）/ 自动压缩次数 / 节省字符
  const contextPct = totals?.context_usage_pct ?? null;
  const compressionCount = totals?.compression_count ?? 0;
  const savedChars = totals?.saved_chars ?? 0;
  // [v1.0.34-M4-v2] 瞬时组（本回合=最新一条含数据的记录）+ 投影保留条数
  const latestCompressionCount = totals?.latest_compression_count ?? null;
  const latestSavedChars = totals?.latest_saved_chars ?? null;
  const latestProjectedCount = totals?.latest_projected_count ?? null;
  const projectedCount = totals?.projected_count ?? 0;
  const hasContextData = hasUsage || compressionCount > 0 || savedChars > 0
    || projectedCount > 0 || latestProjectedCount != null;

  const animatedCalls = useCountUp(totals?.total_calls ?? 0);
  const animatedTokens = useCountUp(totals?.total_tokens ?? 0);

  // 金额卡：优先完整估算；有未定价模型时显示有价部分 + 标注。
  // 主币种跟随界面语言（英文 → USD 大字，中文 → CNY 大字），副显示为另一币种。
  const isEnglish = i18n.language.startsWith('en');
  const costPrimary = isEnglish
    ? (totals?.estimated_cost_usd ?? totals?.priced_cost_usd ?? null)
    : (totals?.estimated_cost_cny ?? totals?.priced_cost_cny ?? null);
  const costSecondary = isEnglish
    ? (totals?.estimated_cost_cny ?? totals?.priced_cost_cny ?? null)
    : (totals?.estimated_cost_usd ?? totals?.priced_cost_usd ?? null);
  const hasUnpriced = Boolean(totals && totals.unpriced_tokens && totals.unpriced_tokens > 0);

  // 自定义日历的本地草稿：未确认前不触发请求。
  const [draftRange, setDraftRange] = useState<{ start: string | null; end: string | null }>({
    start: null,
    end: null,
  });
  useEffect(() => {
    if (customOpen && range.kind === 'custom') {
      setDraftRange({ start: range.start ?? null, end: range.end ?? null });
    }
  }, [customOpen, range]);

  const switchRange = (next: TokenRange): void => {
    if (next.kind === 'custom') {
      setCustomOpen(true);
      return;
    }
    setCustomOpen(false);
    if (projectId) openPanel(projectId, next);
  };

  const confirmCustom = (start: string, end: string): void => {
    setCustomOpen(false);
    if (projectId) openPanel(projectId, { kind: 'custom', start, end });
  };

  const toggleCustom = (): void => {
    setCustomOpen((value) => !value);
  };

  const trendRows = useMemo(() => {
    if (!data) return [];
    const byDate = new Map(data.daily.map((row) => [row.date, row]));
    const result: TokenUsageDaily[] = [];
    if (range.kind === 'custom' && range.start && range.end) {
      const cursor = new Date(`${range.start}T12:00:00`);
      const endDate = new Date(`${range.end}T12:00:00`);
      while (cursor.getTime() <= endDate.getTime()) {
        const key = localDateKey(cursor);
        result.push(byDate.get(key) ?? {
          date: key,
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
          cache_hit_input: 0,
          cache_miss_input: 0,
          calls: 0,
        });
        cursor.setDate(cursor.getDate() + 1);
      }
      return result;
    }
    const count = range.kind === '7d' ? 7 : range.kind === '30d' ? 30
      : range.kind === 'today' ? 1 : data.daily.length;
    if (range.kind === 'total') return data.daily;
    const cursor = new Date();
    cursor.setHours(12, 0, 0, 0);
    cursor.setDate(cursor.getDate() - count + 1);
    for (let index = 0; index < count; index += 1) {
      const key = localDateKey(cursor);
      result.push(byDate.get(key) ?? {
        date: key,
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        cache_hit_input: 0,
        cache_miss_input: 0,
        calls: 0,
      });
      cursor.setDate(cursor.getDate() + 1);
    }
    return result;
  }, [data, range]);

  const modelRows = useMemo(() => (data?.by_model ?? []), [data]);
  // 后端聚合时已移除的 worker 查不到花名册名字，name 回落为 role/agent_id——
  // 用「名字即 ID / 名字即角色」判为历史成员，灰标「已移除」，数据仍留存。
  const agentRows = useMemo(() => {
    const rows = data?.by_agent ?? [];
    return rows.map((row) => {
      const fallbackName = row.name === row.agent_id || row.name === row.role || !row.name;
      return { ...row, removed: fallbackName };
    });
  }, [data]);

  const customLabel = useMemo(() => {
    if (range.kind !== 'custom' || !range.start || !range.end) return '';
    return `${range.start.slice(5)} ~ ${range.end.slice(5)}`;
  }, [range]);

  if (!open) return null;

  return (
    <div
      className={'tk-wrap' + (closing ? ' closing' : '')}
      ref={wrapRef}
      style={panelH != null ? { height: `${panelH}px`, maxHeight: 'none' } : undefined}
    >
      <aside
        className="tk-panel"
        aria-label={t('chat.stream.01')}
        ref={panelRef}
        style={panelH != null ? { maxHeight: 'none' } : undefined}
      >
        {/* ── 筛选栏 40px：今日 / 近7天 / 近30天 / 总计 / 自定义时段（顺序排列）── */}
        <div className="tk-filter">
          <div className="tk-filter-presets" role="tablist" aria-label={t('token.usage.panel.07')}>
            {PRESETS.map((preset) => (
              <button
                key={preset.kind}
                type="button"
                role="tab"
                aria-selected={range.kind === preset.kind}
                className={'tk-filter-btn' + (range.kind === preset.kind ? ' active' : '')}
                onClick={() => switchRange({ kind: preset.kind })}
              >{t(preset.label)}</button>
            ))}
            <div className="tk-filter-custom">
              <button
                ref={triggerRef}
                type="button"
                role="tab"
                aria-selected={range.kind === 'custom'}
                className={'tk-filter-btn tk-custom-btn' + (range.kind === 'custom' ? ' active' : '')}
                aria-expanded={customOpen}
                onClick={toggleCustom}
              >
                {range.kind === 'custom' && customLabel ? customLabel : t('token.usage.panel.17')}
                <span className="tk-caret" aria-hidden="true">▾</span>
              </button>
              {customOpen && (
                <RangeCalendar
                  start={draftRange.start}
                  end={draftRange.end}
                  onConfirm={confirmCustom}
                  onClose={() => setCustomOpen(false)}
                  anchorRef={triggerRef}
                />
              )}
            </div>
          </div>
        </div>

        {/* ── 统计卡区（2×2 左半 + 右侧通栏上下文卡）──
             左上半：请求次数 | 消费金额 左右平分
             左下半：词元 Token 卡（数字居中）
             右侧整列：上下文占用（跨两行） */}
        <div className="tk-cards">
          <StatCard
            className="tk-stat-calls"
            label={t('token.usage.panel.19')}
            value={formatTokens(animatedCalls)}
            delay={0}
          />
          <StatCard
            className="tk-stat-cost"
            label={t('token.usage.panel.15')}
            value={costPrimary == null ? '--' : (isEnglish ? formatUsd(costPrimary) : formatCny(costPrimary))}
            accent
            delay={40}
            sub={(
              <>
                {costSecondary != null && (
                  <span className="tk-card-usd">{isEnglish ? formatCny(costSecondary) : formatUsd(costSecondary)}</span>
                )}
                {hasUnpriced && <span className="tk-card-note">{t('token.usage.panel.24')}</span>}
              </>
            )}
          />
          <StatCard
            className="tk-stat-tokens"
            label={t('token.usage.panel.tokens')}
            value={formatCompact(animatedTokens)}
            delay={80}
          />
          {/* [v1.0.34-M4/M4-v2] 上下文占用：右侧通栏（跨两行）。
              上半一行：合并标题「AI 记忆用量（最近一次对话）」+ 占用率大字，水平居中；
              下半纵向铺满：两个指标各占整行宽 */}
          <div className="tk-card tk-stat-context">
            <div className="tk-context-top">
              <span className="tk-context-label">{t('token.usage.panel.ctxTitle')}</span>
              <span className="tk-context-value">
                {contextPct == null ? '--' : `${Math.round(contextPct)}%`}
              </span>
            </div>
            {hasContextData ? (
              <div className="tk-context-metrics">
                <div className="tk-context-metric">
                  <div className="tk-context-metric-head">
                    <span className="tk-context-metric-name">{t('token.usage.panel.ctxCompress')}</span>
                    <span className="tk-context-metric-hint">{t('token.usage.panel.ctxCompressHint')}</span>
                  </div>
                  <div className="tk-context-metric-row">
                    <b>{t('token.usage.panel.ctxColLatest')}</b> {latestCompressionCount == null ? '--' : <><span className="tk-ctx-num">{latestCompressionCount}</span>{' '}{t('token.usage.panel.ctxTimesUnit')}</>}
                    <span className="tk-ctx-dot">·</span>
                    <b>{t('token.usage.panel.ctxColTotal')}</b> <span className="tk-ctx-num">{compressionCount}</span>{' '}{t('token.usage.panel.ctxTimesUnit')}
                    <span className="tk-ctx-dot">·</span>
                    {t('token.usage.panel.ctxApprox')} <span className="tk-ctx-num">{formatZhCompact(savedChars / ZH_TO_TOKEN_RATIO)}</span>{' '}{t('token.usage.panel.ctxTokensUnit')}
                  </div>
                </div>
                <div className="tk-context-metric">
                  <div className="tk-context-metric-head">
                    <span className="tk-context-metric-name">{t('token.usage.panel.ctxPrune')}</span>
                    <span className="tk-context-metric-hint">{t('token.usage.panel.ctxPruneHint')}</span>
                  </div>
                  <div className="tk-context-metric-row">
                    <b>{t('token.usage.panel.ctxColLatest')}</b> {latestProjectedCount == null ? '--' : <><span className="tk-ctx-num">{formatTokens(latestProjectedCount)}</span>{' '}{t('token.usage.panel.ctxEntriesUnit')}</>}
                    <span className="tk-ctx-dot">·</span>
                    <b>{t('token.usage.panel.ctxColTotal')}</b> <span className="tk-ctx-num">{formatTokens(projectedCount)}</span>{' '}{t('token.usage.panel.ctxEntriesUnit')}
                  </div>
                </div>
              </div>
            ) : (
              <div className="tk-context-empty">{t('token.usage.panel.ctxEmpty')}</div>
            )}
          </div>
        </div>

        {/* ── 趋势图 ── */}
        <div className="tk-section">
          <div className="tk-section-head">
            <span className="tk-section-title">{t('token.usage.panel.20')}</span>
            <div className="tk-legend" aria-hidden="true">
              <span><i className="tk-legend-hit" />{t('token.usage.panel.02')}</span>
              <span><i className="tk-legend-miss" />{t('token.usage.panel.13')}</span>
              <span><i className="tk-legend-out" />{t('token.usage.panel.21')}</span>
            </div>
          </div>
          {hasUsage ? (
            <TrendChart rows={trendRows} />
          ) : (
            <div className="tk-empty">{t('token.usage.panel.12')}</div>
          )}
        </div>

        {/* ── 明细表 ── */}
        <div className="tk-section">
          <div className="tk-detail-tabs" role="tablist" aria-label={t('token.usage.panel.08')}>
            <button
              type="button"
              role="tab"
              aria-selected={detailTab === 'model'}
              className={'tk-tab' + (detailTab === 'model' ? ' active' : '')}
              onClick={() => setDetailTab('model')}
            >{t('token.usage.panel.06')}</button>
            <button
              type="button"
              role="tab"
              aria-selected={detailTab === 'agent'}
              className={'tk-tab' + (detailTab === 'agent' ? ' active' : '')}
              onClick={() => setDetailTab('agent')}
            >{t('token.usage.panel.05')}</button>
          </div>
          <div className="tk-table">
            <div className="tk-table-head" aria-hidden="true">
              <span className="tk-th-name">{t('token.usage.panel.14')}</span>
              <span className="tk-th-calls">{t('token.usage.panel.18')}</span>
              <span className="tk-th-rate">{t('token.usage.panel.16')}</span>
              <span className="tk-th-tokens">{t('token.usage.panel.tokens')}</span>
              <span className="tk-th-cost">{t('token.usage.panel.25')}</span>
            </div>
            {detailTab === 'model' ? (
              modelRows.length ? (
                modelRows.map((row: TokenUsageModel, index: number) => (
                  <DetailRow
                    key={row.model}
                    name={row.model}
                    calls={row.calls}
                    hit={row.cache_hit_input}
                    miss={row.cache_miss_input}
                    tokens={row.total_tokens}
                    cost={isEnglish ? row.estimated_cost_usd : row.estimated_cost_cny}
                    costNote={row.pricing?.source && row.pricing.source !== 'unknown'
                      ? row.pricing.source
                      : undefined}
                    delay={index * 30}
                  />
                ))
              ) : (
                <div className="tk-empty">{t('token.usage.panel.11')}</div>
              )
            ) : (
              agentRows.length ? (
                agentRows.map((row: TokenUsageAgent & { removed?: boolean }, index: number) => (
                  <DetailRow
                    key={row.agent_id}
                    name={row.name || row.agent_id}
                    meta={row.role}
                    calls={row.calls ?? 0}
                    hit={row.cache_hit_input}
                    miss={row.cache_miss_input}
                    tokens={row.total_tokens}
                    cost={isEnglish ? (row.estimated_cost_usd ?? null) : (row.estimated_cost_cny ?? null)}
                    delay={index * 30}
                    removed={row.removed}
                  />
                ))
              ) : (
                <div className="tk-empty">{t('token.usage.panel.09')}</div>
              )
            )}
          </div>
        </div>

        {/* ── 底部时效说明 ── */}
        <div className="tk-foot">
          {t('token.usage.panel.priceNote')}
        </div>
      </aside>

      {/* ── 底部拖拽手柄：调整面板展示视口高度（拉高上限 = 输入区上边界）── */}
      <div
        className="tk-resize"
        role="separator"
        aria-orientation="horizontal"
        aria-label={t('token.usage.panel.resize')}
        tabIndex={0}
        onPointerDown={onResizeStart}
        onKeyDown={onResizeKeyDown}
      >
        <span className="tk-resize-bar" aria-hidden="true" />
      </div>
    </div>
  );
};

export default TokenUsagePanel;
