/**
 * Suggestions.tsx — [v1.0.23.3] 四方向建议卡片（照 apple 参考按钮模块）。
 *
 * - 2×2 网格小卡片：title 主行 + sub 副行
 * - hover 光斑跟随鼠标（--x/--y CSS 变量，radial-gradient）
 * - 点击 → 自动发送（只发 title，sub 是给用户看的说明不进消息）+ 自动 @来源agent
 *   → 逐卡阶梯消失（0/35/70/105ms）+ 容器收起（540ms）
 * - 不点击 → 常驻；用户手动发新消息 → state 层清空（D-5），组件自然卸载
 */

import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SuggestionItem } from '../store/state';

export interface SuggestionsProps {
  items: SuggestionItem[];
  /** 点击卡片发送的完整消息（由上层组装：title + sub + @mention）。 */
  onSend: (text: string) => void;
}

export const Suggestions: React.FC<SuggestionsProps> = ({ items, onSend }) => {
  const { t } = useTranslation();
  const [consuming, setConsuming] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // 已消费（点过）的卡片：逐卡消失后整体收起。
  const handleClick = useCallback(
    (item: SuggestionItem) => (): void => {
      if (consuming) return;
      setConsuming(true);
      const root = rootRef.current;
      if (root) {
        root.style.maxHeight = `${root.scrollHeight}px`;
        void root.offsetHeight; // 强制回流，让 max-height 动画从当前值出发
      }
      onSend(item.title);
      // 容器收起（照参考 540ms）；到时清空 height 内联值（卸载时不再需要）
      window.setTimeout(() => {
        if (root) root.style.maxHeight = '';
      }, 560);
    },
    [consuming, onSend],
  );

  // hover 光斑跟随鼠标：--x/--y 打在卡片根上，::before 的 radial-gradient 跟着走。
  const trackSpot = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>): void => {
      const rect = e.currentTarget.getBoundingClientRect();
      e.currentTarget.style.setProperty('--x', `${e.clientX - rect.left}px`);
      e.currentTarget.style.setProperty('--y', `${e.clientY - rect.top}px`);
    },
    [],
  );

  // 渲染前固定顺序，避免 items 引用变化导致动画 key 抖动
  const cards = useMemo(() => items, [items]);

  return (
    <section
      className={'suggestions' + (consuming ? ' consuming' : '')}
      aria-label={t('suggestions.label')}
      ref={rootRef}
    >
      <div className="suggestions-grid">
        {cards.map((item, index) => (
          <button
            key={`${index}-${item.title.slice(0, 16)}`}
            className="suggestion"
            type="button"
            onClick={handleClick(item)}
            onMouseMove={trackSpot}
            disabled={consuming}
          >
            <span className="suggestion-title">{item.title}</span>
            {item.sub ? <span className="suggestion-sub">{item.sub}</span> : null}
          </button>
        ))}
      </div>
    </section>
  );
};

export default Suggestions;
