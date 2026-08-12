/**
 * ZoomPan.tsx — [v0.44 → v0.44.5 Bug2] 预览视口的统一工具区 +「放大/缩小 + 拖拽平移」外壳
 *
 * 摆放位置：src/components/ZoomPan.tsx
 *
 * 被所有格式的预览窗口复用：
 *   · PreviewPanel（文件预览：Markdown/HTML/图片/PDF/docx/pptx/表格/兜底）
 *   · KnowledgePreview（知识资产 / 技能包 / 画像）
 *
 * ── [v0.44.5 Bug2] 工具区改为「标题栏下方的独立一行」──
 *   原先工具条 position:absolute 悬浮在视口右上角，会遮挡正文，且与 PDF 原生的
 *   【50%/100%/150%/适应宽度】缩放条重叠打架。现在改成 PDF 原生控件那种模式：
 *   一整行功能区，静态排在预览窗口顶部、标题栏之下，占位不悬浮。所有预览类型
 *   共用同一行。左区（.zp-bar-lead）与缩放区（.zp-bar-zoom）都开放为「插槽」，
 *   具体渲染器（如 PDF）可把自己的翻页 / 缩放控件 portal 进来，与本壳的缩放控件
 *   合并到同一行——不再出现两条重叠的工具条。
 *
 * 设计要点（不破坏既有行为是第一位的）：
 *   · **100% 且未开抓手** → 原样直渲：内容照旧走视口自己的滚动条，链接可点、
 *     文字可选、iframe（HTML/PDF）交互如常。
 *   · **缩放 ≠ 100%** → 内容套 transform: translate(tx,ty) scale(z)，视口 overflow:hidden，
 *     按住左键拖拽平移；一层透明拖拽膜盖在内容上（iframe 会吞指针事件）。
 *   · **100% 且内容超出视口** → 工具条上的「抓手」开关打开后，按住拖拽 = 滚动平移。
 *   · Ctrl/⌘ + 滚轮 = 缩放；双击内容区 = 回 100%。
 *   · **渲染器自管缩放时（ownsZoom，PDF 即是）** → 本壳让位：不套 transform、不接管
 *     滚轮/拖拽、隐藏自带缩放簇；缩放区交给该渲染器 portal 进来的原生控件。
 *
 * 缩放档位：50% ~ 300%，按 ×1.2 步进。状态是组件本地的（换文件时外层用 key 重挂）。
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import './zoom-pan.css';

const Z_MIN = 0.5;
const Z_MAX = 3;
const Z_STEP = 1.2;

function clampZoom(z: number): number {
  return Math.min(Z_MAX, Math.max(Z_MIN, z));
}

/** ＋ / － / 抓手 / 复位 的线条图标（stroke 1.7，与全局 lucide 风格一致） */
const ic = (children: React.ReactNode): React.ReactElement => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);
const IcPlus = ic(<path d="M12 5v14M5 12h14" />);
const IcMinus = ic(<path d="M5 12h14" />);
const IcReset = ic(<><path d="M3 12a9 9 0 1 0 2.6-6.4" /><path d="M3 4v4h4" /></>);
const IcHand = ic(
  <path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11m0-4.5a1.5 1.5 0 0 1 3 0V12m0-3a1.5 1.5 0 0 1 3 0v5a7 7 0 0 1-7 7h-.6a7 7 0 0 1-5.8-3.1L3 15.2a1.7 1.7 0 0 1 2.6-2L7 14.6V7a1.5 1.5 0 0 1 3 0" />,
);

/**
 * 工具区插槽上下文：渲染器（如 PDF）用它把自己的控件 portal 进统一工具行。
 *   · leadEl  —— 左区 DOM 节点（放翻页等前导控件）。
 *   · zoomEl  —— 缩放区 DOM 节点（放渲染器的原生缩放控件）。
 *   · setOwnsZoom(true) —— 声明「本渲染器自管缩放」：外壳隐藏自带缩放簇并让位交互。
 */
export interface ZoomPanToolbarSlot {
  leadEl: HTMLElement | null;
  zoomEl: HTMLElement | null;
  setOwnsZoom: (owns: boolean) => void;
}
const ZoomPanToolbarCtx = createContext<ZoomPanToolbarSlot | null>(null);

/** 渲染器调用它拿到工具区插槽（不在 ZoomPanViewport 内时返回 null，渲染器应各自兜底）。 */
export function useZoomPanToolbar(): ZoomPanToolbarSlot | null {
  return useContext(ZoomPanToolbarCtx);
}

interface Props {
  children: React.ReactNode;
  /** 无障碍名（「文件预览」/「知识预览」）。 */
  label?: string;
}

export const ZoomPanViewport: React.FC<Props> = ({ children, label }) => {
  const { t } = useTranslation();
  const [zoom, setZoom] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const [hand, setHand] = useState(false);       // 100% 档的抓手开关
  const [dragging, setDragging] = useState(false);

  // 工具区插槽 DOM 节点（用 state 承接 ref，挂载后触发一次重渲染让 portal 生效）。
  const [leadEl, setLeadEl] = useState<HTMLElement | null>(null);
  const [zoomEl, setZoomEl] = useState<HTMLElement | null>(null);
  // 渲染器是否自管缩放（PDF=true）。true 时本壳隐藏缩放簇、不接管滚轮/拖拽。
  const [ownsZoom, setOwnsZoom] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    x: number; y: number; tx: number; ty: number; sl: number; st: number;
  } | null>(null);

  const zoomed = Math.abs(zoom - 1) > 0.001;

  const applyZoom = useCallback((z: number): void => {
    const next = clampZoom(z);
    setZoom(next);
    if (Math.abs(next - 1) <= 0.001) { setTx(0); setTy(0); }
  }, []);

  const reset = useCallback((): void => {
    setZoom(1); setTx(0); setTy(0);
  }, []);

  // Ctrl/⌘ + 滚轮缩放（passive:false 才能 preventDefault 掉浏览器整页缩放）。
  //   渲染器自管缩放时（ownsZoom）不接管滚轮——交给它自己处理。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || ownsZoom) return undefined;
    const onWheel = (e: WheelEvent): void => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      applyZoom((e.deltaY < 0 ? Z_STEP : 1 / Z_STEP) * zoom);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoom, applyZoom, ownsZoom]);

  // ── 拖拽平移 ──
  //   缩放态：动 translate；100%+抓手：动 scrollLeft/Top。ownsZoom 时整体让位。
  const panActive = !ownsZoom && (zoomed || hand);

  const onPointerDown = useCallback((e: React.PointerEvent): void => {
    if (!panActive || e.button !== 0) return;
    const el = scrollRef.current;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    dragRef.current = {
      x: e.clientX, y: e.clientY, tx, ty,
      sl: el?.scrollLeft ?? 0, st: el?.scrollTop ?? 0,
    };
    setDragging(true);
    e.preventDefault();
  }, [panActive, tx, ty]);

  const onPointerMove = useCallback((e: React.PointerEvent): void => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (zoomed) {
      setTx(d.tx + dx);
      setTy(d.ty + dy);
    } else {
      const el = scrollRef.current;
      if (el) {
        el.scrollLeft = d.sl - dx;
        el.scrollTop = d.st - dy;
      }
    }
  }, [zoomed]);

  const endDrag = useCallback((): void => {
    dragRef.current = null;
    setDragging(false);
  }, []);

  const slot = useMemo<ZoomPanToolbarSlot>(
    () => ({ leadEl, zoomEl, setOwnsZoom }),
    [leadEl, zoomEl],
  );

  return (
    <ZoomPanToolbarCtx.Provider value={slot}>
      <div className="zp-root" aria-label={label}>
        {/* [v0.44.5 Bug2] 统一工具行：静态排在顶部（标题栏下方），不悬浮、不遮挡正文。
            左区 = 渲染器前导控件插槽（PDF 翻页）；缩放区 = 渲染器原生缩放 或 本壳缩放簇。 */}
        <div className="zp-bar" role="toolbar" aria-label={t('zoom.pan.09')}>
          <div className="zp-bar-lead" ref={setLeadEl} />
          <span className="zp-bar-spacer" />
          <div className="zp-bar-zoom" ref={setZoomEl}>
            {!ownsZoom && (
              <>
                <button type="button" className="zp-btn" title={t('zoom.pan.04')} aria-label={t('zoom.pan.04')}
                  onClick={() => applyZoom(zoom / Z_STEP)} disabled={zoom <= Z_MIN + 0.001}>
                  {IcMinus}
                </button>
                <button type="button" className="zp-pct" title={t('zoom.pan.01')} onClick={reset}>
                  {Math.round(zoom * 100)}%
                </button>
                <button type="button" className="zp-btn" title={t('zoom.pan.03')} aria-label={t('zoom.pan.03')}
                  onClick={() => applyZoom(zoom * Z_STEP)} disabled={zoom >= Z_MAX - 0.001}>
                  {IcPlus}
                </button>
                <span className="zp-sep" />
                <button
                  type="button"
                  className={'zp-btn' + (panActive ? ' on' : '')}
                  title={zoomed ? t('zoom.pan.08') : t('zoom.pan.06')}
                  aria-label={t('zoom.pan.07')}
                  aria-pressed={panActive}
                  onClick={() => { if (!zoomed) setHand((v) => !v); }}
                >
                  {IcHand}
                </button>
                <button type="button" className="zp-btn" title={t('zoom.pan.05')}
                  aria-label={t('zoom.pan.02')} onClick={reset}
                  disabled={!zoomed && tx === 0 && ty === 0}>
                  {IcReset}
                </button>
              </>
            )}
          </div>
        </div>

        {/* 视口：100% = 原生滚动；缩放态 = overflow hidden + transform */}
        <div
          ref={scrollRef}
          className={
            'zp-viewport'
            + (zoomed ? ' zoomed' : '')
            + (panActive ? ' pan' : '')
            + (dragging ? ' dragging' : '')
          }
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={() => { if (zoomed) reset(); }}
        >
          <div
            className="zp-content"
            style={zoomed ? {
              transform: `translate(${tx}px, ${ty}px) scale(${zoom})`,
              transformOrigin: '0 0',
            } : undefined}
          >
            {children}
          </div>
          {/* 缩放态的透明拖拽膜：iframe（HTML/PDF）会吞指针事件，膜在上面接住拖拽。 */}
          {zoomed && <div className="zp-dragfilm" aria-hidden="true" />}
        </div>
      </div>
    </ZoomPanToolbarCtx.Provider>
  );
};

export default ZoomPanViewport;
