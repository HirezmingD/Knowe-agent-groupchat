/**
 * ResizeHandle.tsx — 左栏与聊天区之间那条可拖的分界线（v0.5 #14）
 *
 * 做法上有两个刻意的选择：
 *
 * 1. **宽度写进 CSS 变量，不写进 React state。**
 *    拖拽时鼠标每动一个像素就 setState，会把整棵会话列表重渲染一遍——手感是黏的。
 *    这里直接改 `--clist-w` 这个 CSS 变量，React 全程不重渲染，拖起来是跟手的。
 *    只有松手那一下才落一次 state（为了进 localStorage 记住宽度）。
 *
 * 2. **窄到一定程度进「紧凑模式」**（只剩头像，名字藏起来）。
 *    不是把列表压扁到看不清，而是换一种形态——这是它该有的样子，不是它坏了。
 */

import React, { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

export const CLIST_MIN = 76;      // 再窄就只剩一列头像了
export const CLIST_MAX = 420;
export const CLIST_DEFAULT = 332;   // 设计稿的原宽
/** 窄于此 → 紧凑模式（藏名字，只留头像） */
export const CLIST_COMPACT = 200;

const VAR = '--clist-w';

/**
 * [v1.0.24.1] wordmark（左栏顶部 Logo）完整显示所需的最小宽度。
 * 动态测量：img 固有宽高比 × 显示高度 28px（CSS .wordmark img）＋ head 左右 padding ＋ 余量。
 * 用 naturalWidth/naturalHeight（图片固有像素，不受 display:none/紧凑模式影响），
 * 显示高度用 getComputedStyle 解析（CSS 显式 28px，display:none 时仍返回指定值）。
 */
function wordmarkMinWidth(): number {
  const img = document.querySelector<HTMLImageElement>('.clist-head .wordmark img');
  if (!img || !img.naturalWidth || !img.naturalHeight) return 0;
  const head = document.querySelector('.clist-head');
  const cs = head ? getComputedStyle(head) : null;
  const pad = cs ? parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) : 0;
  const imgCs = getComputedStyle(img);
  const h = parseFloat(imgCs.height) || 28;
  return Math.ceil((img.naturalWidth / img.naturalHeight) * h + pad + 4);
}

function clamp(px: number): number {
  // [v1.0.24.1] 最小宽度 = 至少完整显示 wordmark，且不低于紧凑分界（低于分界 wordmark 会被隐藏）
  const min = Math.max(CLIST_MIN, CLIST_COMPACT, wordmarkMinWidth());
  return Math.max(min, Math.min(CLIST_MAX, px));
}

/** 把宽度写到根节点上（同时切紧凑模式的标记） */
export function applyWidth(px: number): void {
  const w = clamp(px);
  const root = document.documentElement;
  root.style.setProperty(VAR, `${w}px`);
  root.classList.toggle('clist-compact', w < CLIST_COMPACT);
}

export interface ResizeHandleProps {
  /** 拖完之后（松手时）回调一次，用来记住宽度 */
  onCommit?: (px: number) => void;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({ onCommit }) => {
  const { t } = useTranslation();
  const dragging = useRef(false);
  const width = useRef(CLIST_DEFAULT);

  const onMove = useCallback((e: PointerEvent) => {
    if (!dragging.current) return;
    // 左栏左边是 Rail，所以宽度 = 鼠标 x - Rail 右边缘
    const railRight = document.querySelector('.rail')?.getBoundingClientRect().right ?? 0;
    width.current = clamp(e.clientX - railRight);
    applyWidth(width.current);
  }, []);

  const onUp = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    document.body.classList.remove('resizing');
    onCommit?.(width.current);
  }, [onCommit]);

  useEffect(() => {
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [onMove, onUp]);

  const start = (e: React.PointerEvent): void => {
    e.preventDefault();
    dragging.current = true;
    document.body.classList.add('resizing');   // 拖的时候全局改光标、禁选中
  };

  /** 键盘也能调（拖拽不是唯一的路——用不了鼠标的人也得能改） */
  const onKeyDown = (e: React.KeyboardEvent): void => {
    const step = e.shiftKey ? 32 : 8;
    if (e.key === 'ArrowLeft') width.current = clamp(width.current - step);
    else if (e.key === 'ArrowRight') width.current = clamp(width.current + step);
    else return;
    e.preventDefault();
    applyWidth(width.current);
    onCommit?.(width.current);
  };

  return (
    <div
      className="clist-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label={t('resize.handle.01')}
      tabIndex={0}
      onPointerDown={start}
      onKeyDown={onKeyDown}
      onDoubleClick={() => {                    // 双击恢复默认——拖坏了有条回头路
        width.current = CLIST_DEFAULT;
        applyWidth(CLIST_DEFAULT);
        onCommit?.(CLIST_DEFAULT);
      }}
    />
  );
};

export default ResizeHandle;
