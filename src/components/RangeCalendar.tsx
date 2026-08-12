// src/components/RangeCalendar.tsx
// [v1.0.20.1-M3] 自定义日期范围选择器（单月视图）。
//
// 交互照抄 nice_ui_components/calendar select（Origin UI DateRangePicker）：
// - 两击交互：第一次点击=起点（仅本地记录，不提交）；第二次点击=终点（提交，自动排序）
// - hover 预览：选完起点后，鼠标悬停任意日期 → 起点~悬停日期范围实时高亮
//   （悬停端 = accent 圆块 + focus 阴影，与起点同款）
// - 视觉：范围中间格 = accent-tint 背景、无圆角（rounded-none）；
//   起点/终点/预览端 = accent 实心圆块白字；今天 = 蓝色实心块；
//   普通日期 hover = 边框反馈；按压 scale(0.97)
// - 弹层动画：scale(0.97)→1 + opacity，200ms ease-out，origin 左上
// - 深/浅双主题全走 var() 令牌；reduced-motion 降级为纯淡入

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

const WEEKDAYS = [
  'date.calendar.wd.02',
  'date.calendar.wd.03',
  'date.calendar.wd.04',
  'date.calendar.wd.05',
  'date.calendar.wd.06',
  'date.calendar.wd.07',
  'date.calendar.wd.01',
];

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

export function localDateKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

interface RangeCalendarProps {
  /** 当前起点（YYYY-MM-DD，可为空） */
  start: string | null;
  /** 当前终点（YYYY-MM-DD，可为空） */
  end: string | null;
  /** 确认选择（第二次点击后调用） */
  onConfirm: (start: string, end: string) => void;
  /** 关闭弹层（点外部 / Esc / 再次点触发器） */
  onClose: () => void;
  /** 触发器元素 ref，用于定位弹层 */
  anchorRef?: React.RefObject<HTMLElement | null>;
}

export const RangeCalendar: React.FC<RangeCalendarProps> = ({
  start,
  end,
  onConfirm,
  onClose,
  anchorRef,
}) => {
  const { t } = useTranslation();
  const today = useMemo(() => new Date(), []);
  const todayKey = localDateKey(today);
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth());
  // 两击状态：picking=false 等起点；picking=true 等终点（draftStart 为已选起点）
  const [picking, setPicking] = useState(false);
  const [draftStart, setDraftStart] = useState<string | null>(null);
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  // 外部传入的 start/end 变化时同步两击状态。
  useEffect(() => {
    if (start && end) {
      setPicking(false);
      setDraftStart(null);
    } else if (start && !end) {
      setPicking(true);
      setDraftStart(start);
    } else {
      setPicking(false);
      setDraftStart(null);
    }
  }, [start, end]);

  // 点外部 / Esc 关闭（弹层自身与触发器不算外部）。
  useEffect(() => {
    const onPointerDown = (event: PointerEvent): void => {
      const target = event.target as Node | null;
      if (!target) return;
      if (target instanceof Element) {
        if (target.closest('[data-rc-calendar]')) return;
        if (anchorRef?.current?.contains(target)) return;
      }
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, anchorRef]);

  const cells = useMemo(() => {
    const first = new Date(viewYear, viewMonth, 1);
    // 周一为一周之首：getDay() 0=周日 → 前导偏移 (getDay()+6)%7
    const lead = (first.getDay() + 6) % 7;
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const result: { key: string; date: Date; inMonth: boolean; disabled: boolean }[] = [];
    for (let index = 0; index < lead; index += 1) {
      const date = new Date(viewYear, viewMonth, index - lead + 1);
      result.push({ key: `prev-${index}`, date, inMonth: false, disabled: true });
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      const date = new Date(viewYear, viewMonth, day);
      const disabled = date.getTime() > today.getTime();
      result.push({ key: `day-${day}`, date, inMonth: true, disabled });
    }
    const tail = 42 - result.length;
    for (let index = 0; index < tail; index += 1) {
      const date = new Date(viewYear, viewMonth + 1, index + 1);
      result.push({ key: `next-${index}`, date, inMonth: false, disabled: true });
    }
    return result;
  }, [viewYear, viewMonth, today]);

  const prevMonth = (): void => {
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear((year) => year - 1);
    } else {
      setViewMonth((month) => month - 1);
    }
  };
  const nextMonth = (): void => {
    const next = new Date(viewYear, viewMonth + 1, 1);
    if (next.getTime() > today.getTime()) return; // 不允许翻到未来月
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear((year) => year + 1);
    } else {
      setViewMonth((month) => month + 1);
    }
  };

  // 两击：第一次设起点（不提交）；第二次确定终点并提交（自动排序，支持单日）。
  const pickDay = (date: Date): void => {
    if (date.getTime() > today.getTime()) return;
    const key = localDateKey(date);
    if (!picking) {
      setDraftStart(key);
      setPicking(true);
      setHoverKey(key);
    } else {
      const base = draftStart ?? key;
      const orderedStart = base <= key ? base : key;
      const orderedEnd = base <= key ? key : base;
      onConfirm(orderedStart, orderedEnd);
      setPicking(false);
      setDraftStart(null);
      setHoverKey(null);
    }
  };

  // 已确认范围 + 预览范围（选终点时 hover 实时高亮）
  const rangeStart = picking ? draftStart : (start || null);
  const rangeEnd = picking ? hoverKey : (end || null);
  const inRange = (key: string): boolean => {
    if (!rangeStart || !rangeEnd) return false;
    return key > rangeStart && key < rangeEnd;
  };
  const isStart = (key: string): boolean => key === rangeStart;
  const isEnd = (key: string): boolean => key === rangeEnd && key !== rangeStart;
  // 预览端：选终点时悬停的那个日期
  const isPreviewEnd = (key: string): boolean => picking && key === hoverKey && hoverKey !== draftStart;

  return (
    <div className="rc-calendar" data-rc-calendar role="dialog" aria-label={t('range.calendar.05')}>
      <div className="rc-head">
        <button
          type="button"
          className="rc-nav"
          aria-label={t('range.calendar.02')}
          onClick={prevMonth}
        >‹</button>
        <div className="rc-title">{t('range.calendar.title', { y: viewYear, m: viewMonth + 1 })}</div>
        <button
          type="button"
          className="rc-nav"
          aria-label={t('range.calendar.03')}
          onClick={nextMonth}
          disabled={new Date(viewYear, viewMonth + 1, 1).getTime() > today.getTime()}
        >›</button>
      </div>
      <div className="rc-week" aria-hidden="true">
        {WEEKDAYS.map((day) => <span key={day}>{t(day)}</span>)}
      </div>
      <div className="rc-grid" role="grid" aria-label={t('range.calendar.01')}>
        {cells.map((cell) => {
          const key = localDateKey(cell.date);
          const classes = [
            'rc-day',
            cell.inMonth ? '' : 'rc-day-out',
            cell.disabled ? 'rc-day-disabled' : '',
            inRange(key) ? 'rc-day-range' : '',
            isStart(key) ? 'rc-day-start' : '',
            isEnd(key) ? 'rc-day-end' : '',
            isPreviewEnd(key) ? 'rc-day-preview' : '',
            key === todayKey ? 'rc-day-today' : '',
          ].filter(Boolean).join(' ');
          return (
            <button
              type="button"
              key={cell.key}
              className={classes}
              role="gridcell"
              disabled={cell.disabled}
              aria-label={key}
              onClick={() => pickDay(cell.date)}
              onMouseEnter={() => {
                if (!cell.disabled) setHoverKey(key);
              }}
            >
              <span className="rc-day-inner">{cell.date.getDate()}</span>
            </button>
          );
        })}
      </div>
      <div className="rc-foot">
        <span className="rc-hint">
          {picking
            ? draftStart
              ? t('range.calendar.pickEnd', { date: draftStart.slice(5) })
              : t('range.calendar.04')
            : start && end
              ? `${start.slice(5)} ~ ${end.slice(5)}`
              : t('range.calendar.04')}
        </span>
        {start && end && (
          <button
            type="button"
            className="rc-clear"
            onClick={() => onConfirm(start, start)}
          >{t('range.calendar.06')}</button>
        )}
      </div>
    </div>
  );
};

export default RangeCalendar;
